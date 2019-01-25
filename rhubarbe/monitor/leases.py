class MonitorLeases:                                    # pylint: disable=r0902

    def __init__(self, message_bus, reconnectable,      # pylint: disable=r0913
                 channel, cycle, step, wait, verbose):
        self.message_bus = message_bus
        self.reconnectable = reconnectable
        self.channel = channel
        self.verbose = verbose

        # the leases part
        self.cycle = float(Config().value('monitor', 'cycle_leases'))
        self.step = float(Config().value('monitor', 'step_leases'))
        self.wait = float(Config().value('monitor', 'wait_leases'))
#        channel = Config().value('sidecar', 'channel_leases')
        self.monitor_leases = MonitorLeases(
            message_bus, self.reconnectable, channel, cycle,
            step=step, wait=wait, verbose=verbose)

    def on_back_channel(self, *args):
        # when anything is received on the backchannel, we go to fast track
        logger.info("MonitorLeases.on_back_channel, args={}".format(args))
        self.fast_track = True                          # pylint: disable=w0201

    async def run_forever(self):
        leases = Leases(self.message_bus)
        while True:
            self.fast_track = False                     # pylint: disable=w0201
            trigger = time.time() + self.cycle
            # check for back_channel every 15 ms
            while not self.fast_track and time.time() < trigger:
                await asyncio.sleep(self.step)
                # give a chance to socketio events to trigger
                self.reconnectable.wait(self.wait)

            try:
                await leases.refresh()
                # xxx this is fragile
                omf_leases = leases.resources
                self.reconnectable.emit_info(self.channel, omf_leases,
                                             wrap_in_list=False)
                logger.info("advertising {} leases".format(len(omf_leases)))
                if self.verbose:
                    logger.info("Leases details: {}".format(omf_leases))
            except Exception:
                logger.exception("monitornodes could not get leases")


if __name__ == '__main__':

    def main():
        # rebootnames = sys.argv[1:]
        message_bus = asyncio.Queue()

        test_url = Config().value('sidecar', 'url')
        reconnectable = ReconnectableSocketIOMonitor(None, test_url,
                                                     verbose=True)
        monitor_leases = MonitorLeases(message_bus,
                                       reconnectable=reconnectable,
                                       channel='info:leases',
                                       cycle=10, step=1, wait=.1, verbose=True)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.gather(monitor_leases.run_forever()))

    main()
