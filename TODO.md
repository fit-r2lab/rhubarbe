# `rhubarbe` *per se*

## images

* improvement: rimages --official or --stable or something
  * for filtering out `saving` images

* improvement: rimages ~foo : show all images that **do not** contain foo

* bugfix:
  use log files in $HOME if permission is denied in current directoy
  maybe add an option to remove the log altogether; what's the use of logging for rimages ??

* rload and rsave could use a more silent mode where the recurring messages 'trying..' and the like just go away

# design (P2)

## asynciojobs

* the `wait` function was tweaked on sep 23 2016 to use asynciojobs instead of plain gather
* this turned out to make it much easier to deal nicely with the endless display job
* it would make sense to use the same approach for other commands some day

## known bugs (P3)

* merge `wait` and `status` in a more general `select` tool
  * --status: show current output of `status`
  * --export: show selected nodes within a `export NODES='blabla'` (for the `nodes` alias)
  * -0/-1: select nodes that are on or off
  * -a: all nodes
  * --wait: select nodes that are ssh-reachable within the timeout

* *not sure* implement some way to store the logs from frisbee and imagezip somewhere
* *not sure* should we not log all the messages on the feedback/bus onto logger as well ?
* *not quite useful* curses react to window resize
  * getch() to return curses.KEY_RESIZE in such a case
  * window.nodelay(1) allows to make getch() non-blocking

