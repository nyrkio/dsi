"""
Utilities for running commands on hosts
"""
import datetime
import inspect
import logging
import os
import sys

import sys

if sys.version_info[:2] >= (3, 8):
    from collections.abc import MutableMapping
else:
    from collections import MutableMapping

from enum import Enum
from functools import partial
import structlog
from dateutil import tz

import common.atlas_setup as atlas_setup
from common.config import ConfigDict
import common.host_factory
import common.host_utils
import common.utils
import common.mongodb_setup_helpers

from common.download_tar import temp_file

from common.thread_runner import run_threads

LOG = logging.getLogger(__name__)
SLOG = structlog.get_logger(__name__)

EXCEPTION_BEHAVIOR = Enum('Exception Behavior', 'CONTINUE RERAISE EXIT')


def prepare_reports_dir(reports_dir='reports'):
    """ Prepare the reports directory to receive test data (logs, diagnostics etc).
    Unlink the current reports directory and remove any tar ball. Then create a timestamped
    directory and sym link to reports_dir
    :param str reports_dir: the reports directory name. Defaults to reports
    :raises OSError: when reports_dir exists and it is a directory.
    """

    if os.path.exists(reports_dir) or os.path.islink(reports_dir):
        os.remove(reports_dir)

    if os.path.exists('../reports.tgz'):
        os.remove('../reports.tgz')

    real_reports_dir = "{}-{}".format(reports_dir, datetime.datetime.now(tz.tzlocal()).isoformat())
    common.utils.mkdir_p(real_reports_dir)
    os.symlink(real_reports_dir, reports_dir)


# pylint: disable=too-many-arguments
def make_host_runner(host_info, command, prefix, config):
    """
    For the host, make an appropriate RemoteHost or LocalHost Object and run the set of commands.

    :param host_info HostInfo
    :param command: The command to execute. If str, run that command. If dict, type is one of
    upload_repo_files, upload_files, retrieve_files, exec, or exec_mongo_shell.
    :type command: str, dict
    :param str prefix: The id for the test related to the current command. If there
    is not a specific test related to the current command, the value of prefix should reflect the
    hook that the command belongs to, such as between_tests, post_task, and so on.
    :param config: top-level ConfigDict
    """
    mongodb_auth_settings = common.mongodb_setup_helpers.mongodb_auth_settings(config)
    # Note: This works because mongodb_setup.meta.net.ssl has the same structure as a mongo node
    # config file, even if it isn't otherwise a config file.
    use_tls = common.mongodb_setup_helpers.mongodb_tls_configured(config['mongodb_setup']['meta'])

    # Create the appropriate host type
    target_host = common.host_factory.make_host(host_info, mongodb_auth_settings, use_tls)
    try:
        # If command is a string, pass it directly to run
        if isinstance(command, str):
            target_host.run(command)

        # If command is a dictionary, parse it
        elif isinstance(command, MutableMapping):
            _run_host_command_map(target_host, command, prefix, config)
    finally:
        target_host.close()


def _run_host_command(host_list, command, config, prefix):
    """
    For each host in the list, make a parallelized call to make_host_runner to make the appropriate
    host and run the set of commands.

    :param list host_list: List of ip addresses to connect to
    :param command: The command to execute. If str, run that command. If dict, type is one of
    upload_repo_files, upload_files, retrieve_files, exec, or exec_mongo_shell.
    :type command: str, dict
    :param ConfigDict config: The system configuration
    :param str prefix: The id for the test related to the current command. If there
    is not a specific test related to the current command, the value of prefix should reflect the
    hook that the command belongs to, such as between_tests, post_task, and so on.
    """
    if not host_list:
        return

    LOG.debug('Calling run command for %s with command %s', str(host_list), str(command))

    thread_commands = []
    for host_info in host_list:
        thread_commands.append(partial(make_host_runner, host_info, command, prefix, config))

    run_threads(thread_commands, daemon=True)


def _run_host_command_map(target_host, command, prefix, config):
    """
    Run one command against a target host if the command is a mapping.

    :param Host target_host: The host to send the command to
    :param MutableMapping command: The command to execute
    :param str prefix: The id for the test related to the current command. If there
    is not a specific test related to the current command, the value of prefix should reflect the
    hook that the command belongs to, such as between_tests, post_task, and so on.

    :raises: UserWarning on error, HostException when there is a cmd or paramiko issue.
    **Note: retrieve_files does not directly raise exceptions on error**.
    """
    # pylint: disable=too-many-branches,too-many-locals,too-many-statements
    for key, value in command.items():
        if key == "upload_repo_files":
            for paths in value:
                source = os.path.join(common.utils.get_dsi_path(), paths['source'])
                target = paths['target']
                LOG.debug('Uploading file %s to %s', source, target)
                target_host.upload_file(source, target)
        elif key == "upload_files":
            for paths in value:
                assert (
                    ('content' in paths and paths['content'])
                    or ('source' in paths and paths['source'])
                ), "upload_files: Either a source file or providing your own content is required."
                source_path = None
                if 'source' in paths:
                    source_path = paths['source']
                else:
                    source_path = temp_file()
                    with open(source_path, "w") as temp_local_file:
                        temp_local_file.write(paths['content'])

                LOG.debug('Uploading file %s to %s', source_path, paths['target'])
                target_host.upload_file(source_path, paths['target'])

        elif key == "retrieve_files":
            for paths in value:
                source = paths['source']
                target = paths['target']
                if prefix:
                    target = os.path.join('reports', prefix, target_host.alias,
                                          os.path.normpath(target))
                else:
                    target = os.path.join('reports', target_host.alias, os.path.normpath(target))

                LOG.debug('Retrieving file %s from %s', source, target)
                target_host.retrieve_path(source, target)
        elif key == "exec":
            LOG.debug('Executing command %s', value)
            success = target_host.run(value)
            common.host_utils.raise_if_not_success(success, value)
        elif key == "exec_mongo_shell":
            LOG.debug('Executing command %s in mongo shell', value)
            connection_string = value.get('connection_string', "")
            exit_status = target_host.exec_mongo_command(value['script'],
                                                         connection_string=connection_string)
            common.host_utils.raise_if_not_ok(exit_status, value)
        elif key == "exec_client_shell":
            LOG.debug('Executing command %s in the client shell', value)
            exit_status = target_host.exec_client_shell(value['script'], config)
            common.host_utils.raise_if_not_ok(exit_status, value)
        elif key == "checkout_repos":
            for paths in value:
                source = paths['source']
                target = paths['target']
                branch = paths['branch'] if 'branch' in paths else None
                verbose = paths['verbose'] if 'verbose' in paths else False
                LOG.debug('Checking out git repository %s to %s', target, source)
                target_host.checkout_repos(source, target, branch, verbose=verbose)
        elif key == "download_files":
            for paths in value:
                source = paths['source']
                target = paths['target']
                verbose = paths['verbose'] if 'verbose' in paths else False
                LOG.debug('Downloading %s to %s', target, source)
                target_host.download(source, target, verbose=verbose)
        elif key == "network_delays":
            # Import here to avoid circular imports
            import common.delays as delays
            delays.establish_host_delays(target_host, command, config, prefix)
        else:
            raise UserWarning("Invalid command type")


def run_host_command(target, command, config, prefix):
    """
    Sets up and runs a command for use on the appropriate hosts.

    :param str target: The target to run the command on
    :param dict command: The action to run
    :param ConfigDict config: The system configuration
    :param str prefix: The id for the test related to the current command. If there
    is not a specific test related to the current command, the value of prefix should reflect the
    hook that the command belongs to, such as between_tests, post_task, and so on.
    """

    assert isinstance(command, MutableMapping), "command isn't a dict"
    assert target.startswith('on_')

    keys = command.keys()
    target = target[3:]
    hosts = common.host_utils.extract_hosts(target, config)
    LOG.info("Running command(s) %s on %s", keys, target)
    _run_host_command(hosts, command, config, prefix)
    LOG.debug("Done running command(s) %s on %s", keys, target)


def run_atlas_command(target, command, config, prefix):
    """
    Execute on_atlas commands with atlas_setup.

    :param str target: The target to run the command on. (Always on_atlas)
    :param dict command: The action to run
    :param ConfigDict config: The system configuration
    :param str prefix: The id for the test related to the current command. If there
    is not a specific test related to the current command, the value of prefix should reflect the
    hook that the command belongs to, such as between_tests, post_task, and so on.
    """
    assert target == "on_atlas"

    keys = command.keys()
    LOG.info("Running command(s) %s on %s", keys, target)
    atlas = atlas_setup.AtlasSetup(config)
    for key in keys:
        if key == "retrieve_logs":
            atlas.download_logs(prefix)
        else:
            raise KeyError("Command {} not supported with on_atlas.".format(key))
    LOG.debug("Done running command(s) %s on %s", keys, target)


def run_host_commands(commands, config, prefix):
    """
    Plural version of run_host_command: run a list of commands.

    Example of commands:

    [
        { 'on_workload_client': { 'upload_files': [{ 'source': 'path', 'target': 'dest' }] } }
    ]

    :param list commands: List of dict actions to run
    :param ConfigDict config: The system configuration
    :param str prefix: The id for the test related to the current command. If there
    is not a specific test related to the current command, the value of prefix should reflect the
    hook that the command belongs to, such as between_tests, post_task, and so on.
    """
    for command in commands:
        # Item should be a map with one entry
        assert isinstance(command, MutableMapping), "command in list isn't a dict"
        assert len(command.keys()) == 1, "command has more than one entry"
        for target, target_command in command.items():
            target = list(command.keys())[0]
            run_host_command(target, target_command, config, prefix)


def make_workload_runner_host(config):
    """
    Convenience function to make a host to connect to the workload runner node.

    :param ConfigDict config: The system configuration
    """
    host_info = common.host_utils.extract_hosts('workload_client', config)[0]
    mongodb_auth_settings = common.mongodb_setup_helpers.mongodb_auth_settings(config)
    # Note: This works because mongodb_setup.meta.net.ssl has the same structure as a mongo node
    # config file, even if it isn't otherwise a config file.
    use_tls = common.mongodb_setup_helpers.mongodb_tls_configured(config['mongodb_setup']['meta'])
    return common.host_factory.make_host(host_info, mongodb_auth_settings, use_tls)


def print_trace(trace, exception):
    """ print exception information for run_pre_post_commands. Information corresponds
    to YAML file tasks

    :param list((frame_object, string, int,
                 string, list(string), int)) trace: returned by inspect.trace()
    Refer to python docs:
    https://docs.python.org/2/library/inspect.html#inspect.trace
    https://docs.python.org/2/library/inspect.html#the-interpreter-stack
    Each element in the list is a "tuple of six items: the frame object, the filename, the line
    number of the current line, the function name, a list of lines of context from the source code,
    and the index of the current line within that list"

    :param Exception() exception: this is the exception raised by one of tasks

    *NOTE* This function is dependent on the stack frames of the function calls made within
    run_pre_post_commands along with the variable names in run_pre_post_commands,
    run_host_command, dispatch_commands, and _run_host_command_map. Changes in the variable names
    or the flow of function calls could cause print_trace to log wrong/unhelpful info.
    """
    top_function = trace[0][3]
    bottom_function = trace[-1][3]
    bottom_function_file = trace[-1][1]
    bottom_function_line = str(trace[-1][2])
    # This conditional does not cause any errors due to lazy evaluation
    if len(trace) > 1 and 'target' in trace[1][0].f_locals:
        executed_task = trace[1][0].f_locals['target']
    else:
        executed_task = ""
    executed_command = {}
    for frame in trace:
        if frame[3] == "run_host_command" and executed_command == {}:
            executed_command = frame[0].f_locals['command']
        # If executed_command is a ConfigDict, you can't set arbitrary keys like this:
        if frame[3] == "_run_host_command_map" and not isinstance(executed_command, ConfigDict):
            executed_command[frame[0].f_locals['key']] = frame[0].f_locals['value']
    error_msg = "Exception originated in: " + bottom_function_file
    error_msg = error_msg + ":" + bottom_function + ":" + bottom_function_line
    error_msg = error_msg + "\n" + "Exception msg: " + str(exception)
    error_msg = error_msg + "\n" + top_function + ":"
    if executed_task != '':
        error_msg = error_msg + "\n    in task: " + executed_task
    if executed_command != {}:
        error_msg = error_msg + "\n" + "        in command: " + str(executed_command)
    LOG.error(error_msg)


def run_upon_error(task, command_dicts, config, exception_behavior=EXCEPTION_BEHAVIOR.EXIT):
    """ Run all commands associated with the 'upon_error' command key. This method exits from the
    script on completion.

    :param str task: task name. For example, 'mongodb_setup'. This value is used to generate the
    output path. eg. 'reports/upon_error/mongodb_setup'.
    :param list(ConfigDict) command_dicts: List of ConfigDict objects that may have the specified
    command_section.
    :param dict(ConfigDict) config: The system configuration.
    :param EXCEPTION_BEHAVIOR exception_behavior: Indicates the proper action to take upon catching
    an exception.
    Note: see :method: `run_pre_post_commands` for more detailed behaviour.
    """
    prepare_reports_dir()
    run_pre_post_commands('upon_error', command_dicts, config, exception_behavior,
                          'upon_error/{}'.format(task))


def run_pre_post_commands(command_key,
                          command_dicts,
                          config,
                          exception_behavior,
                          current_test_id=None):
    ''' Runs all commands with the specified command key. If exit on exception is
    true, exit from the script. Otherwise, print the trace and continue.

    :param str command_key: The key to use to find a command list to execute in each of the
    command_dicts.
    :param list(ConfigDict) command_dicts: List of ConfigDict objects that may have the specified
    command_section.
    :param dict(ConfigDict) config: The system configuration.
    :param EXCEPTION_BEHAVIOR exception_behavior: Indicates the proper action to take upon catching
    an exception.
    :param str current_test_id: Indicates the id for the test related to the current set of
    commands. If there is not a specific test related to the current set of commands, the value of
    current_test_id will be None.
    '''
    SLOG.info("run_pre_post_commands", command_key=command_key, current_test_id=current_test_id)
    for command_dict in command_dicts:
        if command_key in command_dict:
            SLOG.debug("in loop", command_dict=command_dict, command_key=command_key)
            try:
                dispatch_commands(command_key, command_dict[command_key], config, current_test_id)
            except Exception as exception:  #pylint: disable=broad-except
                print_trace(inspect.trace(), exception)
                if exception_behavior == EXCEPTION_BEHAVIOR.RERAISE:
                    raise exception
                if exception_behavior == EXCEPTION_BEHAVIOR.EXIT:
                    LOG.error("Exiting with status code: 1")
                    sys.exit(1)
                if exception_behavior == EXCEPTION_BEHAVIOR.CONTINUE:
                    pass
                else:
                    SLOG.error("Invalid exception_behavior entry",
                               command_dict=command_dict,
                               command_key=command_key,
                               exception_behavior=exception_behavior,
                               exception=exception)


def dispatch_commands(command_key, command_list, config, current_test_id=None):
    ''' Routes commands to the appropriate command runner. The command runner will run the command.

    :param str command_key: The key to use to find a command list to execute in each of the
    command_dicts. Used for error handling only.
    :param list(dict) command_list: A list of commands to run
    :param dict(ConfigDict) config: The system configuration.
    :param str current_test_id: Indicates the id for the test related to the current set of
    commands. If there is not a specific test related to the current set of commands, the value of
    current_test_id will be None.
    '''

    SLOG.debug("dispatch_commands",
               command_key=command_key,
               command_list=command_list,
               current_test_id=current_test_id)
    # Most notably, the prefix is used for directory name under reports/.
    # It is either the test id (fio, ycsb_load...) or the command itself (post_task).
    prefix = current_test_id if current_test_id else command_key

    for item in command_list:
        # Item should be a map with one entry
        SLOG.debug("dispatch_commands", item=item)
        assert isinstance(item, MutableMapping), 'item in list isn\'t a dict'
        assert len(item.keys()) == 1, 'item has more than one entry'
        for target, command in item.items():
            if target == "on_atlas":
                run_atlas_command(target, command, config, prefix)
            elif target.startswith('on_'):
                run_host_command(target, command, config, prefix)
            elif target in ["restart_mongodb", "restart_cluster"]:
                if config["cluster_setup"]["meta"]["product_name"] == "mongodb":
                    # Import here to avoid circular imports
                    import mongodb_setup
                    mongo_controller = mongodb_setup.MongodbSetup(config)
                    clean_db_dir = command['clean_db_dir']
                    clean_logs = command['clean_logs']
                    nodes = command.get('nodes')
                    if not mongo_controller.restart(clean_db_dir, clean_logs, nodes):
                        raise Exception("Error restarting mongodb")
                else:
                    # Import here to avoid circular imports
                    import cluster_setup
                    controller = cluster_setup.ClusterSetup(config)
                    clean_db_dir = command['clean_db_dir']
                    clean_logs = command['clean_logs']
                    nodes = command.get('nodes')
                    if not controller.restart(clean_db_dir, clean_logs, nodes):
                        raise Exception("Error restarting cluster")

            elif target == "network_delays":
                # Repackage so it has same structure as ordinary commands
                run_host_command('on_all_hosts', {"network_delays": command}, config, prefix)
            else:
                raise KeyError("Unknown {} target {}".format(command_key, target))
