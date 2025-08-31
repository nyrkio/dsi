"""Download and install cluster_binary_archive on all nodes."""

from functools import partial
import logging
import os
import re
from uuid import uuid4

#pylint: disable=too-few-public-methods
import common.host_factory
import common.host_utils
from common.thread_runner import run_threads

LOG = logging.getLogger(__name__)


def temp_file(path="dsi_downloaded_tar.tgz", sanitize=lambda s: re.sub(r'[^A-Za-z0-9_\-.]', "", s)):
    """ create a temp file name based using the path as a suffix.
     The basename portion of the path will be sanitized and appended to a random UUID.
     If no path is provided then a name is generated using a default path. Worst case, the code will
     only return a random UUID.

     :param str path: The resource location, it can be a uri a full or a relative path
     :param lambda sanitize: a lambda to sanitize the path by removing unacceptable chars.
     The default lambda removes all chars not matching alphanumerics, '-','_' and '.'.

     :returns str a temp file based on this path
    """
    return "{}{}".format(str(uuid4()), sanitize(os.path.basename(path)))


class DownloadTar(object):
    """Download and install cluster_binary_archive on all nodes."""
    def __init__(self, config):

        self.config = config
        self.cluster_binary_archive = config['cluster_setup'].get('tar_file_url', "")

        LOG.info("Download url is %s", self.cluster_binary_archive)

        self.hosts = []
        for host_info in common.host_utils.extract_hosts('all_hosts', self.config):
            self.hosts.append(common.host_factory.make_host(host_info))

        if self.cluster_binary_archive:
            LOG.debug("DownloadTar initialized with url: %s", self.cluster_binary_archive)

    def download_and_extract(self):
        """Download self.cluster_binary_archive, extract it, and create some symlinks.

        :return: True if no cluster_binary_archive was provided or all the commands completed
                      successfully on all servers.
        """
        if not self.cluster_binary_archive:
            LOG.warning("DownloadTar: download_and_extract() was called, "
                        "but cluster_binary_archive isn't defined.")
            return True
        to_download = []
        for host in self.hosts:
            commands = self._remote_commands(host)
            to_download.append(partial(host.run, commands))

        return all(run_threads(to_download, daemon=True))

    def _remote_commands(self, host):
        extract_dir = self.config['cluster_setup']['directories']['extract_dir']
        bin_dir = self.config["cluster_setup"]["directories"]["bin_dir"]
        cluster_executable = self.config["cluster_setup"]["launch_program"]
        version_check = self.config["cluster_setup"].get("version_check", ['-v', '--version'])
        if not isinstance(version_check, list):
            version_check = [version_check]
        if isinstance(cluster_executable, list):
            cluster_executable = cluster_executable[0]
        tmp_file = temp_file(self.cluster_binary_archive)
        return [['echo', 'Downloading {} to {}.'.format(self.cluster_binary_archive,
                                                        host.hostname)],
                ['rm', '-rf', extract_dir],
                ['rm', '-rf', bin_dir],
                ['rm', '-rf', 'bin'],
                ['mkdir', extract_dir],
                ['curl', '--retry', '10', '-fsS', self.cluster_binary_archive, '-o', tmp_file],
                ['tar', '-C', extract_dir, '-zxf', tmp_file],
                ['rm', '-f', tmp_file],
                ['cd', '..'],
                ['mv', extract_dir+'/*/*', extract_dir],
                ['pwd'],
                ['mkdir', '-p', 'bin'],
                ['ln', '-s', '${PWD}/' + extract_dir + '/bin/*', 'bin/']]
