#!/usr/bin/env python3
"""
Analyze results from test_control.py

Using perf.json and all other log files as input, the main object is to produce the Evergreen
results.json file. While perf.json holds the numeric results of the tests, results.json holds
the information whether a test is considered passed or failed, and textual explanations for
failures.

This file is currently focused on analyzing data from the tests that just completed. A separate
signal_processing package is used to compare results to historical timeseries. Both write to
results.json, but execute independently.

Note: Analysis.py doesn't connect to the cluster anymore. It only operates on files in work
directory, and especially reports/.
"""
import argparse
import sys

import structlog

from test_control import copy_to_reports, print_perf_json
from libanalysis.results import ResultsFile
from common.log import setup_logging
from common.config import ConfigDict
from common.workload_output_parser import parse_test_results, get_supported_parser_types

LOG = structlog.get_logger(__name__)


class ResultsAnalyzer(object):
    """
    Analyze results from test_control.py.
    """
    def __init__(self, config):
        self.failures = 0
        self.config = config
        self.results = ResultsFile(config)

    def analyze_all(self):
        """
        Run all plugins that are configured to run for these tests.
        """
        # Dynamically import and execute checker plugins based on configuration for this task.
        # Note that for simplicity the module name and the function name are the same.
        # Example: from libanalysis.core_files import core_files
        plugins = self._get_plugins()
        for plugin in plugins:
            module = __import__('libanalysis')
            func = getattr(module, plugin)
            func(self.config, self.results)

        self.failures = self.results.write()
        return self.failures

    def _get_plugins(self):
        plugins = []
        plugins.extend(self.config['analysis'].get('checks', []))
        return plugins


def main(argv):
    """ Main function. Parse command line options, and run analysis.

    Note that the return value here determines whether Evergreen considers the entire task passed
    or failed. Non-zero return value means failure.

    :returns: int the exit status to return to the caller (0 for OK)
    """
    parser = argparse.ArgumentParser(description='Analyze DSI test results.')

    parser.add_argument('-d', '--debug', action='store_true', help='enable debug output')
    parser.add_argument('--log-file', help='path to log file')
    args = parser.parse_args(argv)
    setup_logging(args.debug, args.log_file)

    config = ConfigDict('analysis')
    config.load()

    if config['analysis']['recompute_perf_json']:
        for test in config['test_control']['run']:
            if not test['type'] in get_supported_parser_types():
                raise NotImplementedError("No parser exists for test type {}".format(test['type']))
            parse_test_results(test, config)

        perf_json = config['test_control']['perf_json']['path']
        copy_to_reports(reports_dir='reports', perf_json=perf_json)
        # Print perf.json to screen
        print_perf_json(filename=perf_json)

    analyzer = ResultsAnalyzer(config)
    analyzer.analyze_all()
    return 1 if analyzer.failures > 0 else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
