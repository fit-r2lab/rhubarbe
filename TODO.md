# `rhubarbe` *per se*

## images

* improvement: allow to refer to an image using its radical name even if it's a saving image

* improvement: rimages --official or --stable or something
  * for filtering out `saving` images

* improvement: rimages ~foo : show all images that **do not** contain foo


## monitor

* improvement: have the monitor probe for `rhubarbe-image`


## probably not doable anytime soon

* regular users cannot use their certificate to do write (PUT, UPDATE) actions
  * wait for Aris's feedback on this bug; update : no useable feedback...
  * this is what prevents us from offering `leases` to regular users

## cosmetic - known bugs (P4)

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

