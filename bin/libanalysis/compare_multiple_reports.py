"""
analysis.py plugin: Compare multiple reports.

This plugin will traverse all directories called reports-*/, collect perf.json results,
and then output CSV / pandas data frame summaries and also save some nice matplotlibs.

All output files are stored under a new directory `./compare_reports/`
"""
import json
import os
import ruamel.yaml as yaml
import pandas as pd
import numpy as np
from matplotlib import pyplot as plt
from matplotlib import ticker

import structlog

from common.utils import mkdir_p

LOGGER = structlog.get_logger(__name__)

OUTPUT_DIR="compare_reports"

def compare_reports(config, results):
    """
    analysis.py plugin: Compare multiple reports.

    :param ConfigDict config: The global config.
    :param ResultsFile results: Object to add results to. (Not used)
    """

    # Historically there was a directory with terraform, bash, some yaml files...
    # To separate deployment mess from results, the latter were put in a directory called reports/
    # When we started using dsi actively, we realized you may want to run multiple tests out of the same
    # work directory, so instead of deleting it each time, we renamed it to reports-YYYY-MM-DDThh:...
    # with the original "reports" now a symlink to the most recent reports-timestamp directory.
    results_prefix="reports-"
    result_data = {}
    csv_results = {}
    metrics = []
    tasks = []
    out_dirs = []

    mkdir_p(OUTPUT_DIR)

    for result_dir in list(filter(lambda x: results_prefix == x[:8], os.listdir("./"))):
        isotimestamp = result_dir[8:]
        day_minute = isotimestamp[5:16].replace(":", "").replace("-","")
        # The main output file with throughput, latency, and other metrics in one nice place
        perf_json = os.path.join(result_dir, "perf.json")
        result_data[result_dir] = {"perf_json":None}
        try:
            with open(perf_json) as perf_json_fh:
                dsi_results = json.load(perf_json_fh)
                result_data[result_dir]["perf_json"] = dsi_results
        except FileNotFoundError as e:
            LOGGER.debug(str(type(e)) + " " + str(e))
            continue

        test_control = os.path.join(result_dir, "test_control.yml")
        try:
            with open(test_control) as tc_fh:
                tc = yaml.load(tc_fh, Loader=yaml.Loader)
                result_data[result_dir]["test_control"] = tc
        except FileNotFoundError as e:
            LOGGER.warning(str(type(e)) + " " + str(e))

        task = config["test_control"]["task_name"] + "_" + day_minute
        tasks.append(task)

        for result in result_data[result_dir]["perf_json"]["results"]:
            workload = result["name"]
            for k,v in result.items():
                if k == "results":
                    for kk, vv in v.items():
                        threads = int(kk)
                        for metric, value in vv.items():
                            if type(value) == "list" or metric[-7:] == "_values":
                                continue

                            if workload not in csv_results:
                                csv_results[workload]={}
                            if metric not in csv_results[workload]:
                                csv_results[workload][metric]={}
                            if task not in csv_results[workload][metric]:
                                csv_results[workload][metric][task]={}
                            csv_results[workload][metric][task][threads]=value
                            metrics.append(metric)

    LOGGER.info("Comparing reports for following tasks: ", tasks=tasks)
    metrics = list(set(metrics))
    for metric in metrics:
        # all tasknames
        tasks = []
        thread_levels = []
        for w,v in csv_results.items():
            if not metric in v:
                continue

            for task, foo in v[metric].items():
                tasks.append(task)
                thread_levels.extend( foo.keys() )


        tasks = sorted(list(set(tasks)))
        thread_levels = sorted(list(set(thread_levels)))

        for workload, v in csv_results.items():
            title = workload + " " + metric
            rows=[]
            if not metric in v:
                continue
            for task, vv in sorted(v[metric].items()):
                values = sorted(vv.items())
                csvrow= [task]
                for t in thread_levels:
                    csvrow.append(vv.get(t, None))

                rows.append(csvrow)
                # print("{}\t{}".format(
                #   task,
                #   "\t".join([str(x) for x in csvrow])))

            out_dir = agraph(workload, metric, tasks, thread_levels, rows)
            out_dirs.append(out_dir)

    LOGGER.info("Wrote comparison data and graphs at...", out_dirs=sorted(list(set(out_dirs))))



def agraph(workload, metric, labels, thread_levels, rows):
    title = workload + " " + metric
    out_dir = OUTPUT_DIR + "/" + workload
    mkdir_p(out_dir)
    # Force same nr of elements
    rowlength = max([len(x)-1 for x in rows])
    length = max(rowlength, len(thread_levels))

    rowmap={}
    for row in rows:
        rowmap[row[0]]=row[1:]


    zeros=[0]*length
    mat = []
    sparsemat = []
    sparse_labels=[]
    for label in labels:
        row = rowmap.get(label, zeros)
        mat.append(row)
        if label in rowmap.keys():
            sparsemat.append(row)
            sparse_labels.append(label)

    thread_levels.sort()
    thread_levels_str = [str(t) for t in thread_levels]
    # df = pd.DataFrame(np.transpose(mat), index=thread_levels, columns=labels)
    df = pd.DataFrame(np.transpose(sparsemat), index=thread_levels, columns=sparse_labels)
    pd.options.display.max_columns = 50
    pd.options.display.width=200
    with open(out_dir + "/pandas.csv", "w") as csv_out:
        out_data = pd.DataFrame(np.transpose(sparsemat), index=thread_levels, columns=sparse_labels)
        csv_out.write(str(out_data))
    colors=["royalblue","tan","red","blueviolet","orange","yellow","lightblue","gray","darkgreen","tomato","brown"]
    ax=df.transpose().plot.bar(stacked=False, figsize=(32,16), color=colors, title=title, grid = True)

    ax.legend(loc='best', fontsize=24)
    ax.tick_params(axis='both', which='major', labelsize=24, length=10)
    ax.tick_params(axis='both', which='minor', labelsize=24)
    ax.tick_params(axis='x', labelrotation=-90)
    plt.tight_layout()
    f=ax.get_figure()
    f.savefig(out_dir + "/bar-"+title+".png")

    ax=df.plot.line(figsize=(32,16), color=colors, title=title, grid=True, use_index=True, logx=True)

    ax.legend(loc='best', fontsize=24)
    ax.tick_params(axis='both', which='major', labelsize=24, length=10)
    ax.tick_params(axis='both', which='minor', labelsize=24)
    ax.tick_params(axis='x', labelrotation=0, )

    ax.set_xticks(thread_levels)
    ax.set_xticklabels(thread_levels_str)

    plt.tight_layout()
    f=ax.get_figure()
    f.savefig(out_dir + "/line-"+title+".png")
    return out_dir


