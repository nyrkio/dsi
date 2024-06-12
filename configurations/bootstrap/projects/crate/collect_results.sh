# Helper bash functions to fetch and consolidate results from several DSI work directories.
#
# Consider a scenario where you ran TSBS benchmark against a database. You used several different
# DSI work directories, and in each you modified some aspect of the database tuning. This has
# resulted in the following directory structure:
#
# project/
#  tsbs_crate_config1/
#      reports-2024:06:12.../
#          ....
#  tsbs_crate-config2/
#      reports-YYY:MM:HH.../
#          ...
#
#  The supplied functions can be used to collect results from all the reports- direct:
#
#  $ set_defaults
#  $ all_load
#  $ all_query     <- Just fetch all results from all subdirectories
#  $ all_pivot     <- Reorder so all results for a given test, from different configurations, are grouped
#
#
#

set_defaults () { load_results="tsbs_load_hack/mongod.0/tsbs_load_hack_pretest.log"; results="test_output.log"; skip=4; limit=1;}

all_load () { for name in $(ls */reports-*/"$load_results"| cut -d / -f 1|sort|uniq); do echo $name; for name2 in $(ls $name/reports-*/$load_results | cut -d / -f 2 |sort|uniq); do echo $name2; tail -n 6 $name/$name2/"$load_results";    done; done }



all_query () { for name in $(ls */reports-*/*/"$results" | cut -d / -f 1|sort|uniq); do echo ... $name ...; for name2 in $(ls $name/reports-*/*/"$results" | cut -d / -f 2|sort|uniq); do echo $name2; for name3 in $(ls $name/$name2/*/"$results" | cut -d / -f 3|sort|uniq); do echo $name3; tail -n $skip $name/$name2/$name3/"$results"|head -n $limit;    done; done done ;}


all_pivot () { for name3 in $(ls */reports-*/*/"$results" | cut -d / -f 3|sort|uniq); do echo ... $name3 ...; for name2 in $(ls */reports-*/$name3/"$results" | cut -d / -f 2|sort|uniq); do for name in $(ls */$name2/*/"$results" | cut -d / -f 1|sort|uniq); do echo "                                                                                                                                  $name $name2" ; tail -n $skip $name/$name2/$name3/"$results" |head -n $limit;    done; done done ;}


filter_load () { egrep 'rows/sec|tsbs-';}




all_load | filter_load
skip=5 limit=2 all_query |egrep '\.\.\.|reports-|count\:\ 1000|tsbs_'
skip=4 limit=1 all_pivot
