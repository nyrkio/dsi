"""
analysis.py plugin: convert perf.json to perf.csv.
"""
import json
import os
import ruamel.yaml as yaml

import structlog
import pandas as pd
import numpy as np
from matplotlib import pyplot as plt

from common.utils import mkdir_p

LOGGER = structlog.get_logger(__name__)

# pylint: disable=too-many-nested-blocks
# pylint: disable=too-many-branches
# pylint: disable=too-many-statements
# pylint: disable=too-many-locals
def json2csv(config, results):
    """
    analysis.py plugin: convert json 2 csv

    :param ConfigDict config: The global config.
    :param ResultsFile results: Object to add results to. (Not used)
    """

    result_data = {}
    csv_results = {}
    metrics = []
    tasks = []
    result_dir = config['test_control']['reports_dir_basename']
    # The main output file with throughput, latency, and other metrics in one nice place
    perf_json = os.path.join(result_dir, "perf.json")
    result_data = None
    try:
        with open(perf_json) as perf_json_fh:
            dsi_results = json.load(perf_json_fh)
            result_data = dsi_results
    except json.decoder.JSONDecodeError:
        LOGGER.warn("Couldn't open the perf.json file.", file_name=perf_json)
        return

    task = config["test_control"]["task_name"]

    for result in result_data["results"]:
        workload = result["name"]
        for k, v in result.items():
            if k == "results":
                for kk, vv in v.items():
                    threads = int(kk)
                    for metric, value in vv.items():
                        if isinstance(value, list) or metric[-7:] == "_values":
                            continue

                        if workload not in csv_results:
                            csv_results[workload] = {}
                        if metric not in csv_results[workload]:
                            csv_results[workload][metric] = {}
                        csv_results[workload][metric][threads] = value

    metrics = []
    thread_levels = []
    for v in csv_results.values():
        for k, obj in v.items():
            metrics.append(k)
            for t in obj.keys():
                thread_levels.append(t)

    thread_levels = list(set(thread_levels))
    metrics = list(set(metrics))

    output_str = ""
    for workload, metrics in csv_results.items():
        output_str += "\n\n" + workload + "\n"
        output_str += "threads;" + ";".join(list(metrics.keys())) + "\n"

        for t in thread_levels:
            output_str += str(t)
            for m in metrics:
                output_str += ";"+str(csv_results[workload][m][t])

    with open(result_dir + "/perf.csv", "w") as csv_out:
        csv_out.write(output_str)
    LOGGER.info("Wrote csv version of perf.json at...", file_name=result_dir+"/perf.csv")

