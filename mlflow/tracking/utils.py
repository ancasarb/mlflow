from __future__ import print_function

import os
import sys

from six.moves import urllib

from mlflow.store import DEFAULT_LOCAL_FILE_AND_ARTIFACT_PATH
from mlflow.store.dbmodels.db_types import DATABASE_ENGINES
from mlflow.store.file_store import FileStore
from mlflow.store.rest_store import RestStore, DatabricksRestStore
from mlflow.tracking.registry import TrackingStoreRegistry
from mlflow.utils import env, rest_utils
from mlflow.utils.file_utils import path_to_local_file_uri
from mlflow.utils.databricks_utils import get_databricks_host_creds

_TRACKING_URI_ENV_VAR = "MLFLOW_TRACKING_URI"
_LOCAL_FS_URI_PREFIX = "file:///"
_REMOTE_URI_PREFIX = "http://"

# Extra environment variables which take precedence for setting the basic/bearer
# auth on http requests.
_TRACKING_USERNAME_ENV_VAR = "MLFLOW_TRACKING_USERNAME"
_TRACKING_PASSWORD_ENV_VAR = "MLFLOW_TRACKING_PASSWORD"
_TRACKING_TOKEN_ENV_VAR = "MLFLOW_TRACKING_TOKEN"
_TRACKING_INSECURE_TLS_ENV_VAR = "MLFLOW_TRACKING_INSECURE_TLS"

_tracking_uri = None


def is_tracking_uri_set():
    """Returns True if the tracking URI has been set, False otherwise."""
    if _tracking_uri or env.get_env(_TRACKING_URI_ENV_VAR):
        return True
    return False


def set_tracking_uri(uri):
    """
    Set the tracking server URI. This does not affect the
    currently active run (if one exists), but takes effect for successive runs.

    :param uri:

                - An empty string, or a local file path, prefixed with ``file:/``. Data is stored
                  locally at the provided file (or ``./mlruns`` if empty).
                - An HTTP URI like ``https://my-tracking-server:5000``.
                - A Databricks workspace, provided as the string "databricks" or, to use a
                  Databricks CLI
                  `profile <https://github.com/databricks/databricks-cli#installation>`_,
                  "databricks://<profileName>".
    """
    global _tracking_uri
    _tracking_uri = uri


def get_tracking_uri():
    """
    Get the current tracking URI. This may not correspond to the tracking URI of
    the currently active run, since the tracking URI can be updated via ``set_tracking_uri``.

    :return: The tracking URI.
    """
    global _tracking_uri
    if _tracking_uri is not None:
        return _tracking_uri
    elif env.get_env(_TRACKING_URI_ENV_VAR) is not None:
        return env.get_env(_TRACKING_URI_ENV_VAR)
    else:
        return path_to_local_file_uri(os.path.abspath(DEFAULT_LOCAL_FILE_AND_ARTIFACT_PATH))


def _is_local_uri(uri):
    """Returns true if this is a local file path (/foo or file:/foo)."""
    scheme = urllib.parse.urlparse(uri).scheme
    return uri != 'databricks' and (scheme == '' or scheme == 'file')


def _is_http_uri(uri):
    scheme = urllib.parse.urlparse(uri).scheme
    return scheme == 'http' or scheme == 'https'


def _is_databricks_uri(uri):
    """Databricks URIs look like 'databricks' (default profile) or 'databricks://profile'"""
    scheme = urllib.parse.urlparse(uri).scheme
    return scheme == 'databricks' or uri == 'databricks'


def _get_file_store(store_uri, **_):
    return FileStore(store_uri, store_uri)


def _get_sqlalchemy_store(store_uri, artifact_uri):
    from mlflow.store.sqlalchemy_store import SqlAlchemyStore
    if artifact_uri is None:
        artifact_uri = DEFAULT_LOCAL_FILE_AND_ARTIFACT_PATH
    return SqlAlchemyStore(store_uri, artifact_uri)


def _get_rest_store(store_uri, **_):
    def get_default_host_creds():
        return rest_utils.MlflowHostCreds(
            host=store_uri,
            username=os.environ.get(_TRACKING_USERNAME_ENV_VAR),
            password=os.environ.get(_TRACKING_PASSWORD_ENV_VAR),
            token=os.environ.get(_TRACKING_TOKEN_ENV_VAR),
            ignore_tls_verification=os.environ.get(_TRACKING_INSECURE_TLS_ENV_VAR) == 'true',
        )

    return RestStore(get_default_host_creds)


def get_db_profile_from_uri(uri):
    """
    Get the Databricks profile specified by the tracking URI (if any), otherwise
    returns None.
    """
    parsed_uri = urllib.parse.urlparse(uri)
    if parsed_uri.scheme == "databricks":
        return parsed_uri.netloc
    return None


def _get_databricks_rest_store(store_uri, **_):
    profile = get_db_profile_from_uri(store_uri)
    return DatabricksRestStore(lambda: get_databricks_host_creds(profile))


_tracking_store_registry = TrackingStoreRegistry()
_tracking_store_registry.register('', _get_file_store)
_tracking_store_registry.register('file', _get_file_store)
_tracking_store_registry.register('databricks', _get_databricks_rest_store)

for scheme in ['http', 'https']:
    _tracking_store_registry.register(scheme, _get_rest_store)

for scheme in DATABASE_ENGINES:
    _tracking_store_registry.register(scheme, _get_sqlalchemy_store)

_tracking_store_registry.register_entrypoints()


def _get_store(store_uri=None, artifact_uri=None):
    return _tracking_store_registry.get_store(store_uri, artifact_uri)


# TODO(sueann): move to a projects utils module
def _get_git_url_if_present(uri):
    """
    Return the path git_uri#sub_directory if the URI passed is a local path that's part of
    a Git repo, or returns the original URI otherwise.
    :param uri: The expanded uri
    :return: The git_uri#sub_directory if the uri is part of a Git repo,
             otherwise return the original uri
    """
    if '#' in uri:
        # Already a URI in git repo format
        return uri
    try:
        from git import Repo, InvalidGitRepositoryError, GitCommandNotFound, NoSuchPathError
    except ImportError as e:
        print("Notice: failed to import Git (the git executable is probably not on your PATH),"
              " so Git SHA is not available. Error: %s" % e, file=sys.stderr)
        return uri
    try:
        # Check whether this is part of a git repo
        repo = Repo(uri, search_parent_directories=True)

        # Repo url
        repo_url = "file://%s" % repo.working_tree_dir

        # Sub directory
        rlpath = uri.replace(repo.working_tree_dir, '')
        if (rlpath == ''):
            git_path = repo_url
        elif (rlpath[0] == '/'):
            git_path = repo_url + '#' + rlpath[1:]
        else:
            git_path = repo_url + '#' + rlpath
        return git_path
    except (InvalidGitRepositoryError, GitCommandNotFound, ValueError, NoSuchPathError):
        return uri
