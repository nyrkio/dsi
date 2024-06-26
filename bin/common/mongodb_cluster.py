"""
Classes to control MongoDB clusters.
"""

from functools import partial
import json
import logging
import os
import sys
import time

import ruamel.yaml as yaml

from common import host_factory
from common.host_utils import ssh_user_and_key_file
from common.models.host_info import HostInfo
from common.config import copy_obj
from common.thread_runner import run_threads

# pylint: disable=too-many-instance-attributes
import common.mongodb_setup_helpers as mongodb_setup_helpers

LOG = logging.getLogger(__name__)

DEFAULT_MEMBER_PRIORITY = 1
# mongodb_setup.topology.0.configsvr ended up being a list, so DEFAULT_CSRS_NAME is in fact the
# hard coded replicaSet name.
DEFAULT_CSRS_NAME = 'configSvrRS'


def create_cluster(topology, config):
    """
    Create MongoNode, ReplSet, or ShardCluster from topology config
    :param topology: topology config to create - see MongoNode, ReplSet, ShardedCluster docs
    :param config: root ConfigDict
    """
    cluster_type = topology['cluster_type']
    LOG.info('creating topology: %s', cluster_type)
    if cluster_type == 'standalone':
        return MongoNode(topology=topology, config=config)
    elif cluster_type == 'replset':
        return ReplSet(topology=topology, config=config)
    elif cluster_type == 'sharded_cluster':
        return ShardedCluster(topology=topology, config=config)
    LOG.fatal('unknown cluster_type: %s', cluster_type)
    return sys.exit(1)


# I want to use self.id for nodes here.
# pylint: disable=invalid-name
class MongoCluster(object):
    """ Abstract base class for mongo clusters """
    def __init__(self, topology, config):
        """
        :param topology: Cluster specific configuration
        :param ConfigDict config: root ConfigDict

        """
        self.config = config
        self.topology = topology

    def wait_until_up(self):
        """ Checks to make sure node is up and accessible"""
        raise NotImplementedError()

    def launch(self, initialize=True, use_numactl=True, enable_auth=False, nodes=None):
        """ Start the cluster """
        raise NotImplementedError()

    def shutdown(self, max_time_ms, auth_enabled=None, retries=20, nodes=None):
        """ Shutdown the cluster gracefully """
        raise NotImplementedError()

    def destroy(self, max_time_ms, nodes=None):
        """ Kill the cluster """
        raise NotImplementedError()

    def setup_host(self, restart_clean_db_dir=None, restart_clean_logs=None, nodes=None):
        """Ensures necessary files are setup

        :param restart_clean_db_dir Should we clean db dir on restart. If not specified, uses value
        from ConfigDict.
        :param restart_clean_logs   Should we clean logs and diagnostic data. If not specified,
        uses value from ConfigDict.
        """
        raise NotImplementedError()

    def run_mongo_shell(self, js_string, max_time_ms=None, dump_on_error=True):
        """
        Run JavaScript code in a mongo shell on the underlying host
        :param str js_string: the javascript to evaluate.
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        :param bool dump_on_error: print 100 lines of mongod.log on error
        :return: True if the mongo shell exits successfully
        """
        raise NotImplementedError()

    def add_default_users(self):
        """
        Add the default users.

        Required for authentication to work properly. Assumes that the cluster is already up and
        running. It must connect to the appropriate node before authentication is enabled. Once the
        users are added, the cluster is rebooted with authentication enabled and any connections
        from there on out must use the authentication string.
        """
        mongodb_setup_helpers.add_user(self, self.config)

    def __str__(self):
        """ String describing the cluster """
        raise NotImplementedError()

    def close(self):
        """Closes SSH connections to remote hosts."""
        raise NotImplementedError()


class MongoNode(MongoCluster):
    """Represents a mongo[ds] program on a remote host."""
    def __init__(self, topology, config):
        """
        :param topology: Read-only options for mongo[ds], example:
        {
            'public_ip': '127.0.0.1',
            'private_ip': '127.0.0.1',
            'config_file': {},
            'mongo_dir': '/usr/bin',
            'priority': 10,
            'clean_logs': True,
            'clean_db_dir': True,
            'is_mongos': False,
            'is_configsvr': False
        }
        :param config: root ConfigDict
        """
        super(MongoNode, self).__init__(topology, config)

        self.id = topology.get('id')
        self.is_mongos = topology.get('is_mongos', False)
        self.is_configsvr = topology.get('is_configsvr', False)
        self.mongo_program = 'mongos' if self.is_mongos else 'mongod'
        self.public_ip = topology['public_ip']
        self.private_ip = topology.get('private_ip', self.public_ip)
        mongodb_setup = self.config['mongodb_setup']
        # Get the mongo_dir from the topology if set, otherwise from the mongo_dir.
        # It now must come from configuration.
        self.bin_dir = os.path.join(topology.get('mongo_dir', mongodb_setup.get('mongo_dir')),
                                    'bin')
        self.use_tls = mongodb_setup_helpers.mongodb_tls_configured(topology['config_file'])

        # NB: we could specify these defaults in default.yml if not already!
        # TODO: https://jira.mongodb.org/browse/PERF-1246 For the next 3 configs, ConfigDict does
        #       not "magically" combine the common setting with the node specific one (topology vs
        #       mongodb_setup). We should add that to ConfigDict to make these lines as simple as
        #       the rest.
        self.clean_logs = topology.get('clean_logs', mongodb_setup.get('clean_logs', True))
        self.clean_db_dir = topology.get('clean_db_dir', (not self.is_mongos)
                                         and mongodb_setup.get('clean_db_dir', True))

        self.mongo_config_file = copy_obj(topology.get('config_file', {}))
        self.logdir = os.path.dirname(self.mongo_config_file['systemLog']['path'])
        self.port = self.mongo_config_file['net']['port']

        self.auth_enabled = False

        if self.is_mongos:
            self.dbdir = None
        else:
            self.dbdir = self.mongo_config_file['storage']['dbPath']

        self.numactl_prefix = self.config['infrastructure_provisioning'].get('numactl_prefix', '')

        # The numactl_prefix is used in shell scripts directly invoked from yaml configs as a shell
        # variable, so it has to be a string. Ideally we want the configuration for numactl to
        # already be a list of commandline arguments.
        if isinstance(self.numactl_prefix, str):
            self.numactl_prefix = self.numactl_prefix.split(' ')

        self.shutdown_options = json.dumps(
            copy_obj(self.config['mongodb_setup']['shutdown_options']))

        # Accessed via @property
        self._host = None

    # This is a @property versus a plain self.host var for 2 reasons:
    # 1. We don't need to be doing SSH stuff or be reading related
    #    configs if we never actually access the host var, and the host
    #    constructors eagerly do this stuff.
    # 2. It makes things slightly easier to test :)
    @property
    def host(self):
        """Access to remote or local host."""
        if self._host is None:
            host_info = self._compute_host_info()
            self._host = host_factory.make_host(host_info, use_tls=self.use_tls)
        return self._host

    def _compute_host_info(self):
        """Create host wrapper to run commands."""
        (ssh_user, ssh_key_file) = ssh_user_and_key_file(self.config)
        return HostInfo(public_ip=self.public_ip,
                        private_ip=self.private_ip,
                        ssh_user=ssh_user,
                        ssh_key_file=ssh_key_file)

    def wait_until_up(self):
        """ Checks to make sure node is up and accessible"""
        js_string = '''
            i = 0
            while (db.serverStatus().ok != 1 && i < 20) {{
                print ("Waiting for node {} to come up");
                sleep(1000);
                i += 1; }}
            assert(db.serverStatus().ok == 1)'''
        i = 0
        while not self.run_mongo_shell(js_string.format(self.public_ip)) and i < 10:
            i += 1
            time.sleep(1)
        if i == 10:
            LOG.error("Node %s not up at end of wait_until_up", self.public_ip)
            return False
        return True

    def setup_host(self, restart_clean_db_dir=None, restart_clean_logs=None, nodes=None):
        if isinstance(nodes, list) and self.id not in nodes:
            # Launch request is limited to some nodes, and this node isn't one of them.
            return True

        self.host.kill_mongo_procs()

        if restart_clean_db_dir is not None:
            _clean_db_dir = restart_clean_db_dir
        else:
            _clean_db_dir = self.clean_db_dir
        _clean_logs = restart_clean_logs if restart_clean_logs is not None else self.clean_logs

        _journal_dir = self.config['mongodb_setup'].get('journal_dir', False)
        if self.is_mongos or self.is_configsvr:
            _journal_dir = False

        # NB: self.dbdir is None when self.is_mongos is True
        setup_cmd_args = {
            'clean_db_dir': _clean_db_dir,
            'clean_logs': _clean_logs,
            'dbdir': self.dbdir,
            'journal_dir': _journal_dir,
            'logdir': self.logdir,
            'is_mongos': self.is_mongos
        }
        LOG.debug("setup_cmd_args:")
        LOG.debug(setup_cmd_args)
        commands = MongoNode._generate_setup_commands(setup_cmd_args)
        return self.host.run(commands)

    @staticmethod
    def _generate_setup_commands(setup_args):
        assert not (setup_args['is_mongos'] and setup_args['journal_dir']), \
            "Internal error: Mongoses cannot have a journal_dir."
        _diagnostic_data_dir = ""
        if setup_args['is_mongos']:
            _diagnostic_data_dir = os.path.join(setup_args['logdir'], 'mongos.diagnostic.data')
        else:
            _diagnostic_data_dir = os.path.join(setup_args['dbdir'], 'diagnostic.data')
        commands = []

        # Clean the logs and diagnostic data
        if setup_args['clean_logs']:
            commands.append(['rm', '-rf', os.path.join(setup_args['logdir'], '*.log')])
            commands.append(['rm', '-rf', os.path.join(setup_args['logdir'], 'core.*')])
            commands.append(['rm', '-rf', os.path.join(_diagnostic_data_dir, '*')])
        # Create the data/logs directories
        commands.append(['mkdir', '-p', setup_args['logdir']])

        # NB: dbdir is None when is_mongos is True
        if setup_args['dbdir'] and setup_args['clean_db_dir']:
            # Deleting diagnostic.data is governed by clean_logs. Don't delete it here.
            # When diagnostic.data doesn't exist, just create an empty one to avoid errors
            commands.append(['mkdir', '-p', os.path.join(setup_args['dbdir'], 'diagnostic.data')])
            commands.append(['rm', '-rf', os.path.join(setup_args['logdir'], 'diagnostic.data')])
            commands.append(
                ['mv',
                 os.path.join(setup_args['dbdir'], 'diagnostic.data'), setup_args['logdir']])

            commands.append(['rm', '-rf', setup_args['dbdir']])

            if setup_args['journal_dir']:
                commands.append(['rm', '-rf', setup_args['journal_dir']])

            commands.append(['mkdir', '-p', setup_args['dbdir']])

            commands.append(
                ['mv',
                 os.path.join(setup_args['logdir'], 'diagnostic.data'), setup_args['dbdir']])

            # If not clean_db_dir assume that this has already been done.
            # Create separate journal directory and link to the database
            if setup_args['journal_dir']:
                commands.append(['mkdir', '-p', setup_args['journal_dir']])
                commands.append([
                    'ln', '-s', setup_args['journal_dir'],
                    os.path.join(setup_args['dbdir'], 'journal')
                ])

        return commands

    # pylint: disable=unused-argument
    def launch_cmd(self, use_numactl=True, enable_auth=False):
        """Returns the command to start this node."""
        remote_file_name = '/tmp/mongo_port_{0}.conf'.format(self.port)
        config_contents = yaml.dump(self.mongo_config_file, default_flow_style=False)
        self.host.create_file(remote_file_name, config_contents)
        self.host.run(['cat', remote_file_name])

        cmd = [os.path.join(self.bin_dir, self.mongo_program), "--config", remote_file_name]

        if use_numactl and self.numactl_prefix:
            if not isinstance(self.numactl_prefix, list):
                raise ValueError('numactl_prefix must be a list of commands, given: {}'.format(
                    self.numactl_prefix))
            cmd = self.numactl_prefix + cmd

        LOG.debug("cmd is %s", str(cmd))
        return cmd

    def launch(self, initialize=True, use_numactl=True, enable_auth=False, nodes=None):
        """
        Starts this node.

        :param boolean initialize: Initialize the node. This doesn't do anything for the
                                     base node
        """
        if isinstance(nodes, list) and self.id not in nodes:
            # Launch request is limited to some nodes, and this node isn't one of them.
            return True

        # initialize is explicitly not used for now for a single node. We may want to use it in
        # the future
        _ = initialize
        self.auth_enabled = enable_auth
        launch_cmd = self.launch_cmd(use_numactl=use_numactl, enable_auth=enable_auth)
        if not self.host.run(launch_cmd):
            LOG.error("failed launch command: %s", launch_cmd)
            self.dump_mongo_log()
            return False
        return self.wait_until_up()

    def run_mongo_shell(self, js_string, max_time_ms=None, dump_on_error=True):
        """
        Run JavaScript code in a mongo shell on the underlying host
        :param str js_string: the javascript to evaluate.
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        :param bool dump_on_error: print 100 lines of mongod.log on error
        :return: True if the mongo shell exits successfully
        """
        remote_file_name = '/tmp/mongo_port_{0}.js'.format(self.port)
        # Value of auth_enabled changes during the lifetime of a MongoNode, so we have to tell
        # the host about auth settings on a case by case basis.
        if self.auth_enabled:
            self.host.mongodb_auth_settings = mongodb_setup_helpers.mongodb_auth_settings(
                self.config)
        else:
            self.host.mongodb_auth_settings = None

        if self.host.exec_mongo_command(js_string,
                                        remote_file_name=remote_file_name,
                                        connection_string="localhost:" + str(self.port),
                                        max_time_ms=max_time_ms) != 0:
            # Some functions call this in a loop, so we may not want to dump the same log repeatedly
            if dump_on_error:
                self.dump_mongo_log()
            return False
        return True

    def dump_mongo_log(self):
        """Dump the mongo[ds] log file to the process log"""
        LOG.info('Dumping log for node %s', self.hostport_public())
        self.host.run(['tail', '-n', '100', self.mongo_config_file['systemLog']['path']])

    def hostport_private(self):
        """Returns the string representation this host/port."""
        return '{0}:{1}'.format(self.private_ip, self.port)

    connection_string_private = hostport_private

    def hostport_public(self):
        """Returns the string representation this host/port."""
        return '{0}:{1}'.format(self.public_ip, self.port)

    connection_string_public = hostport_public

    def shutdown(self, max_time_ms, auth_enabled=None, retries=20, nodes=None):
        """
        Shutdown the node gracefully.

        For the max_time_ms parameter, see :method:`Host.exec_command`
        :return: True if shutdownServer command ran successfully.
        """
        if isinstance(nodes, list) and self.id not in nodes:
            # Shutdown request is limited to some nodes, and this node isn't one of them.
            return True

        if auth_enabled is not None:
            self.auth_enabled = auth_enabled
        for i in range(retries):
            # If there's a problem, don't dump 20x100 lines of log
            dump_on_error = i < 2
            try:
                self.run_mongo_shell(
                    'db.getSiblingDB("admin").shutdownServer({})'.format(self.shutdown_options),
                    max_time_ms=max_time_ms,
                    dump_on_error=dump_on_error,
                )
            except Exception as exc:  # pylint: disable=broad-except
                if str(exc).find("MongoNetworkError") >= 0:
                    # This happens because server shut down faster than shell could disconnect.
                    pass
                else:
                    LOG.error(
                        "Error shutting down MongoNode at %s:%s",
                        self.public_ip,
                        self.port,
                    )

            if self.host.run(["pgrep -l", "mongo"]):
                LOG.warning(
                    "Mongo %s:%s did not shutdown yet",
                    self.public_ip,
                    self.port,
                )
            else:
                return True
            time.sleep(1)
        return False

    def destroy(self, max_time_ms, nodes=None):
        """Kills the remote mongo program. First it sends SIGTERM every second for up to
        max_time_ms. It also always sends a SIGKILL and cleans up dbdir if this attribute is set.

        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        :return: bool True if there are no processes matching 'mongo' on completion.
        """
        if isinstance(nodes, list) and self.id not in nodes:
            # Shutdown request is limited to some nodes, and this node isn't one of them.
            return True

        return_value = False
        try:
            return_value = self.host.kill_mongo_procs(signal_number='SIGTERM',
                                                      max_time_ms=max_time_ms)
        finally:
            if not return_value:
                LOG.warning(
                    "Mongo %s:%s did not shutdown cleanly! Will now SIGKILL and delete lock file.",
                    self.public_ip,
                    self.port,
                )
            # ensure the processes are dead and cleanup
            return_value = self.host.kill_mongo_procs()

            if self.dbdir:
                self.host.run(['rm', '-rf', os.path.join(self.dbdir, 'mongod.lock')])

        return return_value

    def close(self):
        """Closes SSH connections to remote hosts."""
        self.host.close()

    def __str__(self):
        """String describing this node"""
        return '{}: {}'.format(self.mongo_program, self.hostport_public())


class ReplSet(MongoCluster):
    """Represents a replica set on remote hosts."""

    replsets = 0
    """Counts the number of ReplSets created."""
    def __init__(self, topology, config):
        """
        :param topology: Read-only options for  replSet, example:
        {
            'id': 'replSetName',
            'configsvr': False,
            'mongod': [MongoNode topology, ...],
        }
        :param config: root ConfigDict
        """
        super(ReplSet, self).__init__(topology, config)

        self.id = topology.get('id')
        if not self.id:
            self.id = 'rs{}'.format(ReplSet.replsets)
            ReplSet.replsets += 1

        self.rs_conf = topology.get('rs_conf', {})
        self.rs_conf_members = []
        self.nodes = []

        for opt in topology['mongod']:
            # save replica set member configs
            self.rs_conf_members.append(copy_obj(opt.get('rs_conf_member', {})))
            # Must add replSetName and clusterRole
            config_file = copy_obj(opt.get('config_file', {}))
            config_file = mongodb_setup_helpers.merge_dicts(
                config_file, {'replication': {
                    'replSetName': self.id
                }})

            mongod_opt = copy_obj(opt)
            if topology.get('configsvr', False):
                config_file = mongodb_setup_helpers.merge_dicts(
                    config_file, {'sharding': {
                        'clusterRole': 'configsvr'
                    }})
                mongod_opt['is_configsvr'] = True

            mongod_opt['config_file'] = config_file
            self.nodes.append(MongoNode(topology=mongod_opt, config=self.config))

    def highest_priority_node(self):
        """
        Returns the highest priority node.

        Requires all self.nodes[] to have a 'priority' field explicitly set, to work correctly.
        See :method:`ReplSet._set_explicit_priorities`
        """
        max_node = self.nodes[0]
        max_priority = -1
        for node, member in zip(self.nodes, self.rs_conf_members):
            if 'priority' in member and member['priority'] > max_priority:
                max_node = node
                max_priority = member['priority']
        return max_node

    def wait_until_up(self):
        """ Checks and waits for all nodes in replica set to be either PRIMARY or SECONDARY"""
        primary_js_string = '''
            i = 0;
            while (!rs.isMaster().ismaster && i < 120) {{
                print("Waiting for expected primary to become master... attempt = " + i);
                sleep(1000);
                i += 1;
            }}
            assert(rs.isMaster().ismaster);
            rs.slaveOk();
            print("rs.status(): " + tojson(rs.status()));
            print("rs.config(): " + tojson(rs.config()));'''
        # Wait for Primary to be up
        primary = self.highest_priority_node()
        if not self.run_mongo_shell(primary_js_string):
            LOG.error("RS Node %s not up as primary", primary.public_ip)
            return False

        js_string = '''
            i = 0
            while(!rs.isMaster().ismaster && !rs.isMaster().secondary && i < 20) {{
                print ("Waiting for node {} to come up");
                sleep(1000);
                i += 1; }}'''
        # Make sure all nodes are primary or secondary
        for node in self.nodes:
            if not node.run_mongo_shell(js_string.format(node.public_ip)):
                LOG.error("RS Node %s not up at end of wait_until_up", node.public_ip)
                return False
        return True

    def setup_host(self, restart_clean_db_dir=None, restart_clean_logs=None, nodes=None):
        if isinstance(nodes, list) and self.id in nodes:
            # Launch everything in this replset, no need for caller to list every node separately
            nodes = None

        return all(
            run_threads([
                partial(node.setup_host,
                        restart_clean_db_dir=restart_clean_db_dir,
                        restart_clean_logs=restart_clean_logs,
                        nodes=nodes) for node in self.nodes
            ],
                        daemon=True))

    def launch(self, initialize=True, use_numactl=True, enable_auth=False, nodes=None):
        """
        Starts the replica set.

        :param boolean initialize: Initialize the replica set
        """
        if isinstance(nodes, list) and self.id in nodes:
            # Launch everything in this replset
            nodes = None

        if not all(
                run_threads([
                    partial(node.launch,
                            initialize,
                            use_numactl=use_numactl,
                            enable_auth=enable_auth,
                            nodes=nodes) for node in self.nodes
                ],
                            daemon=True)):
            return False
        self._set_explicit_priorities()
        if initialize:
            if not self.run_mongo_shell(self._init_replica_set()):
                return False
        # Wait for all nodes to be up
        return self.wait_until_up()

    def _set_explicit_priorities(self):
        """To make other things easier, we set explicit priorities for all replica set nodes."""
        # Give the first host the highest priority so it will become
        # primary. This is the default behavior.
        if not 'priority' in self.rs_conf_members[0]:
            self.rs_conf_members[0]['priority'] = DEFAULT_MEMBER_PRIORITY + 1
        for member in self.rs_conf_members:
            if not 'priority' in member:
                member['priority'] = DEFAULT_MEMBER_PRIORITY

    def _init_replica_set(self):
        """Return the JavaScript code to configure the replica set."""
        LOG.info('Configuring replica set: %s', self.id)
        config = mongodb_setup_helpers.merge_dicts(self.rs_conf, {'_id': self.id, 'members': []})
        if self.topology.get('configsvr', False):
            config['configsvr'] = True
        for i, node in enumerate(self.nodes):
            member_conf = mongodb_setup_helpers.merge_dicts(self.rs_conf_members[i], {
                '_id': i,
                'host': node.hostport_private()
            })
            config['members'].append(member_conf)
        json_config = json.dumps(config)
        js_string = '''
            config = {0};
            assert.commandWorked(rs.initiate(config),
                                 "Failed to initiate replica set!");
            '''.format(json_config)
        return js_string

    def run_mongo_shell(self, js_string, max_time_ms=None, dump_on_error=True):
        """
        Run JavaScript code in a mongo shell on the primary
        :param str js_string: the javascript to evaluate.
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        :param bool dump_on_error: print 100 lines of mongod.log on error
        :return: True if the mongo shell exits successfully
        """
        primary = self.highest_priority_node()
        return primary.run_mongo_shell(js_string, max_time_ms, dump_on_error)

    def add_default_users(self):
        """
        See :method:`MongoCluster.add_default_user`.
        On a replset we set the write conern to the total number of nodes in the replset to ensure
        the user is added to all nodes during setup.
        """
        mongodb_setup_helpers.add_user(self, self.config, write_concern=len(self.nodes))

    def shutdown(self, max_time_ms, auth_enabled=None, retries=20, nodes=None):
        """Shutdown gracefully
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        """
        if isinstance(nodes, list) and self.id in nodes:
            # Shutdown everything in this replset
            nodes = None

        return all(
            run_threads([
                partial(node.shutdown, max_time_ms, auth_enabled, retries, nodes)
                for node in self.nodes
            ],
                        daemon=True))

    def destroy(self, max_time_ms, nodes=None):
        """Kills the remote replica members.
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        """
        if isinstance(nodes, list) and self.id in nodes:
            # Shutdown everything in this replset
            nodes = None

        run_threads([partial(node.destroy, max_time_ms, nodes) for node in self.nodes], daemon=True)

    def close(self):
        """Closes SSH connections to remote hosts."""
        run_threads([node.close for node in self.nodes], daemon=True)

    def connection_string(self, hostport_fn):
        """Returns the connection string using the hostport_fn function"""
        rs_str = ['{0}/{1}'.format(self.id, hostport_fn(self.nodes[0]))]
        for node in self.nodes[1:]:
            rs_str.append(hostport_fn(node))
        return ','.join(rs_str)

    def connection_string_private(self):
        """Returns the string representation this replica set."""
        return self.connection_string(lambda node: node.hostport_private())

    def connection_string_public(self):
        """Returns the public string representation this replica set."""
        return self.connection_string(lambda node: node.hostport_public())

    def __str__(self):
        """String describing this ReplSet"""
        return 'ReplSet: {}'.format(self.connection_string_public())


class ShardedCluster(MongoCluster):
    """Represents a sharded cluster on remote hosts."""
    def __init__(self, topology, config):
        """
        :param topology: Read-only options for a sharded cluster:
        {
            'disable_balancer': False,
            'configsvr_type': 'csrs',
            'mongos': [MongoNodeConfig, ...],
            'configsvr': [MongoNodeConfig, ...],
            'shard': [ReplSetConfig, ...]
        }
        :param config: root ConfigDict
        """
        super(ShardedCluster, self).__init__(topology, config)

        self.id = topology.get('id')
        self.disable_balancer = topology.get('disable_balancer', True)
        self.mongos_opts = topology['mongos']

        config_type = topology.get('configsvr_type', 'csrs')
        if config_type != 'csrs':
            raise NotImplementedError('configsvr_type: {}'.format(config_type))

        config_opt = {'id': DEFAULT_CSRS_NAME, 'configsvr': True, 'mongod': topology['configsvr']}
        self.config_svr = ReplSet(topology=config_opt, config=config)

        self.shards = []
        self.mongoses = []

        for topo in topology['shard']:
            self.shards.append(create_cluster(topology=topo, config=config))

        for topo in topology['mongos']:
            # add the connection string for the configdb
            config_file = copy_obj(topo.get('config_file', {}))
            configdb_yaml = {'sharding': {'configDB': self.config_svr.connection_string_private()}}
            config_file = mongodb_setup_helpers.merge_dicts(config_file, configdb_yaml)
            mongos_opt = copy_obj(topo)
            mongos_opt['config_file'] = config_file
            mongos_opt['is_mongos'] = True
            self.mongoses.append(MongoNode(topology=mongos_opt, config=self.config))

    def wait_until_up(self):
        """Checks to make sure sharded cluster is up and
        accessible. Specifically checking that the mognos's are up"""
        num_shards = len(self.shards)
        js_string = '''
            db = db.getSiblingDB("config");
            i = 0;
            while (db.shards.find().itcount() < {0} && i < 10) {{
                print ("Waiting for mongos {1} to see {0} shards attempt= " + i);
                sleep(1000);
                i += 1; }}
            assert (db.shards.find().itcount() == {0}) '''
        for mongos in self.mongoses:
            if not mongos.run_mongo_shell(js_string.format(num_shards, mongos.public_ip)):
                LOG.error("Mongos %s does not see right number of shards at end of wait_until_up",
                          mongos.public_ip)
                return False
        return True

    def setup_host(self, restart_clean_db_dir=None, restart_clean_logs=None, nodes=None):
        if isinstance(nodes, list) and self.id in nodes:
            # Launch everything in this sharded cluster
            nodes = None

        commands = [
            partial(self.config_svr.setup_host,
                    restart_clean_db_dir=restart_clean_db_dir,
                    restart_clean_logs=restart_clean_logs,
                    nodes=nodes)
        ]
        commands.extend(
            partial(
                shard.setup_host,
                restart_clean_db_dir=restart_clean_db_dir,
                restart_clean_logs=restart_clean_logs,
                nodes=nodes,
            ) for shard in self.shards)
        commands.extend(
            partial(
                mongos.setup_host,
                restart_clean_db_dir=restart_clean_db_dir,
                restart_clean_logs=restart_clean_logs,
                nodes=nodes,
            ) for mongos in self.mongoses)
        return all(run_threads(commands, daemon=True))

    def launch(self, initialize=True, use_numactl=True, enable_auth=False, nodes=None):
        """Starts the sharded cluster.

        :param boolean initialize: Initialize the cluster
        """
        if isinstance(nodes, list) and self.id in nodes:
            # Launch everything in this sharded cluster
            nodes = None

        LOG.info('Launching sharded cluster...')
        commands = [
            partial(self.config_svr.launch,
                    initialize=initialize,
                    use_numactl=False,
                    enable_auth=enable_auth,
                    nodes=nodes)
        ]
        commands.extend(
            partial(shard.launch,
                    initialize=initialize,
                    use_numactl=use_numactl,
                    enable_auth=enable_auth,
                    nodes=nodes) for shard in self.shards)
        commands.extend(
            partial(mongos.launch,
                    initialize=initialize,
                    use_numactl=use_numactl,
                    enable_auth=enable_auth,
                    nodes=nodes) for mongos in self.mongoses)
        if not all(run_threads(commands, daemon=True)):
            return False
        if initialize:
            if not self._add_shards():
                return False
        if self.disable_balancer and not self.run_mongo_shell('sh.stopBalancer();'):
            return False
        return self.wait_until_up()

    def _add_shards(self):
        """Adds each shard to the cluster."""
        LOG.info('Configuring sharded cluster...')
        # Add shard to mongos
        js_add_shards = []
        for shard in self.shards:
            js_add_shards.append('assert.commandWorked(sh.addShard("{0}"));'.format(
                shard.connection_string_private()))
        if not self.run_mongo_shell('\n'.join(js_add_shards)):
            LOG.error('Failed to add shards!')
            return False
        return True

    def run_mongo_shell(self, js_string, max_time_ms=None, dump_on_error=True):
        """
        Run JavaScript code in a mongo shell on the cluster
        :param str js_string: the javascript to evaluate.
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        :param bool dump_on_error: print 100 lines of mongod.log on error
        :return: True if the mongo shell exits successfully
        """
        return self.mongoses[0].run_mongo_shell(js_string, max_time_ms, dump_on_error)

    def add_default_users(self):
        """
        See :method:`MongoCluster.add_default_user`.
        On a sharded cluster we must add the default user to the config servers and each of the
        shards, in addition to the mongos.
        """
        super(ShardedCluster, self).add_default_users()
        self.config_svr.add_default_users()
        for shard in self.shards:
            shard.add_default_users()

    def shutdown(self, max_time_ms, auth_enabled=None, retries=20, nodes=None):
        """Shutdown the mongodb cluster gracefully.
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        """
        if isinstance(nodes, list) and self.id in nodes:
            # Shutdown everything in this shard
            nodes = None

        commands = []
        commands.extend(
            partial(shard.shutdown, max_time_ms, auth_enabled, retries, nodes)
            for shard in self.shards)
        commands.append(partial(self.config_svr.shutdown, max_time_ms, auth_enabled, retries,
                                nodes))
        commands.extend(
            partial(mongos.shutdown, max_time_ms, auth_enabled, retries, nodes)
            for mongos in self.mongoses)
        return all(run_threads(commands, daemon=True))

    def destroy(self, max_time_ms, nodes=None):
        """Kills the remote cluster members.
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        """
        if isinstance(nodes, list) and self.id in nodes:
            # Shutdown everything in this shard
            nodes = None

        run_threads([partial(shard.destroy, max_time_ms, nodes) for shard in self.shards],
                    daemon=True)
        self.config_svr.destroy(max_time_ms, nodes)
        run_threads([partial(mongos.destroy, max_time_ms, nodes) for mongos in self.mongoses],
                    daemon=True)

    def close(self):
        """Closes SSH connections to remote hosts."""
        run_threads([shard.close for shard in self.shards], daemon=True)
        self.config_svr.close()
        run_threads([mongos.close for mongos in self.mongoses], daemon=True)

    def __str__(self):
        """String describing the sharded cluster"""
        description = ['ShardedCluster:', 'configsvr: {}'.format(self.config_svr)]
        for shard in self.shards:
            description.append('shard: {}'.format(shard))
        for mongos in self.mongoses:
            description.append(str(mongos))
        return '\n'.join(description)
