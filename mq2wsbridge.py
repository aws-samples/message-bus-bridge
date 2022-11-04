#!/usr/bin/env python3
#
# mq2wsbridge.py
#
# MQ to WebSocket message bus bridge - service to bridge messages between two message bus technologies.
#
# 2022-03-16, Brian J. Bernstein
#
# Copyright 2022 Amazon.com and its affiliates; all rights reserved.
# This file is AWS Content and may not be duplicated or distributed without permission.
#

import argparse
import socket
import sys
import time
from threading import Event
from threading import Thread

import boto3

import mqhandler
import wshandler
from confighandler import BridgeConfig
from loghandler import LogHandler
from metricshandler import MetricsHandler

# CONFIG: START

# time to wait for joining MQ and WebSocket threads at shutdown (in seconds)
JOIN_TIMEOUT = 10

# CONFIG: END

VERSION = "v1.0.0"


class Mq2WsBridge:

    def __init__(self):
        self.mq_handler = None
        self.ws_handler = None
        self.ws_stub = False

        self.interrupt_secs = 60
        self.int_sleeper = None
        self.server_running = False

        self.logger = LogHandler()
        self.config_file = "mq2wsbridge.ini"
        self.config_ssm = None

        self.metrics = MetricsHandler(self)

    def quit(self, signo, _frame):
        """
        Attempts to shut down the service. Called by the signal handler (e.g. Ctrl-C).
        :param signo: Signal received from OS.
        :param _frame: Frame.
        :return:
        """
        print(f"Quit signal caught ({signo}); setting shutdown flag.")
        self.server_running = False
        self.int_sleeper.set()
        return

    def interrupter(self, sleeper):
        """
        Thread which sleeps for self.interrupt_secs before setting the subscription loop to False, thus causing it
        to shut down. If self.interrupt_secs is set to 0, then thread will run forever and intent is that it will
        be aborted by a self.int_sleeper.set() call.
        :sleeper: Event object that does the sleeping.
        :return:
        """

        if self.interrupt_secs == 0:
            while self.server_running is True:
                sleeper.wait(60)
        else:
            sleeper.wait(self.interrupt_secs)
            self.logger.verbose("Interrupter invoked to attempt to shut down mq2ws bridge")
            self.server_running = False

        self.logger.debug("Closing MQ handler")
        self.mq_handler.close()
        self.logger.debug("Closing WebSocket handler")
        self.ws_handler.close()
        self.logger.debug("Closing metrics handler")
        self.metrics.close()
        self.logger.debug("Interrupter complete")
        return

    def mysleep(self, secs: int, announce: bool = True):
        """
        Simple sleep routine that can display how many seconds it is sleeping for if debug print enabled.
        :param secs: Number of seconds to sleep.
        :param announce: True if the sleep should be announced to the debug log.
        :return:
        """
        if announce is True:
            self.logger.debug(f"(sleeping {secs} seconds)")
        time.sleep(secs)
        return

    def read_config(self, cli_args):
        """
        Establishes the configuration handler and reads the configuration.
        Additionally, it supports the migration of an INI to SSM.
        :param cli_args: Command line arguments from which to extract INI, SSM, modes, etc.
        :return: Handle to configuration handler.
        """
        config = BridgeConfig(self.config_file, self.config_ssm)
        if cli_args.ssm_migrate is True:
            self.logger.verbose("Migrating INI configuration to Systems Manager")
            if config.convert_ini_to_ssm() is True:
                self.logger.verbose("SSM Configuration Migration successful")
            else:
                self.logger.error("SSM Configuration Migration failed!")

        if self.config_ssm is not None:
            self.logger.debug(f"Reading config from SSM in region '{self.config_ssm}'")
        else:
            self.logger.debug(f"Reading config from INI file '{self.config_file}'")
        config.read_config()
        return config

    def setup_mqhandler(self, config):
        """
        Sets up the MQ handler and thread.
        The intent is that the caller will start() the thread after it is returned to them.

        :param config: Handle to the config object.
        :return: Thread for the MQ handler or None if there was an issue creating the handler.
        """
        # Set up MQ service
        self.logger.verbose("Establishing MQ service...")

        try:
            self.mq_handler = mqhandler.MQHandler()
            self.mq_handler.logger = self.logger
            self.mq_handler.metrics = self.metrics
            self.mq_handler.setup_mqhandler(config)

            mq_thread = Thread(name="MQ_thread", target=self.mq_handler.run_mq_server)
            return mq_thread
        except socket.gaierror:
            self.logger.error("Unable to connect to MQ broker; is network available?")
            return None
        except Exception as e:
            self.logger.error(f"Got unexpected exception opening MQ broker connection: {e}")
            return None

    def setup_wshandler(self, config):
        """
        Sets up the WebSocket handler and thread.
        The intent is that the caller will start() the thread after it is returned to them.

        :param config: Handle to the config object.
        :return: Thread for the WebSocket handler.
        """
        # Set up WebSocket handler
        self.logger.verbose("Establishing WebSocket handler...")
        ws_thread = None

        self.ws_handler = wshandler.WSHandler()
        self.ws_handler.logger = self.logger
        self.ws_handler.metrics = self.metrics

        self.ws_handler.ws_api_host = config.ws_api_host
        self.ws_handler.ws_api_uri = config.ws_api_uri
        self.ws_handler.ws_credentials = {
            'clientId': config.ws_client_id,
            'clientSecret': config.ws_client_password
        }

        if config.ws_ping_interval is None:
            config.ws_ping_interval = 10
            self.logger.warn(f"No WS ping interval specified; using default {config.ws_ping_interval}")
        self.ws_handler.ws_ping_interval = int(config.ws_ping_interval)

        if config.ws_max_connect_attempts is None:
            config.ws_max_connect_attempts = 10
            self.logger.warn(f"No WS max connect attempts specified; using default {config.ws_max_connect_attempts}")
        self.ws_handler.ws_max_connect_attempts = int(config.ws_max_connect_attempts)

        if config.ws_attempt_window_secs is None:
            config.ws_attempt_window_secs = 60
            self.logger.warn(f"No WS attempt window seconds specified; using default {config.ws_attempt_window_secs}")
        self.ws_handler.ws_attempt_window_secs = int(config.ws_attempt_window_secs)

        # are we using STUB WebSocket handler?
        if self.ws_stub is True:
            # set method for incoming MQ to outgoing WebSocket stub
            self.mq_handler.mq_to_ws_method = self.ws_handler.send_ws_message_stub

            # set method for incoming WebSocket stub to outgoing MQ
            self.ws_handler.on_message_handler = self.mq_handler.send_message_from_ws
            ws_thread = Thread(name="wsstub_thread", target=self.ws_handler.run_ws_server_stub)

        else:
            # set method for incoming MQ to outgoing WebSocket
            self.mq_handler.mq_to_ws_method = self.ws_handler.send_ws_message

            # set method for incoming WebSocket to outgoing MQ
            self.ws_handler.on_message_handler = self.mq_handler.send_message_from_ws
            ws_thread = Thread(name="ws_thread", target=self.ws_handler.run_ws_server)

        return ws_thread


def main():
    print(f"MQ to WebSocket Message Bridge {VERSION} starting up")
    sys.stdout.flush()
    bb = Mq2WsBridge()
    default_region = boto3.session.Session().region_name

    parser = argparse.ArgumentParser(description='MQ to WebSocket message bridge service.')
    parser.add_argument('--runsecs', dest='run_secs', default=int(60), type=int,
                        help='number of seconds to run before shutting down, 0=forever (default: %(default)s)')
    parser.add_argument('-v', '--verbose', dest='verbose', default=False, action='store_true',
                        help='display lots of information while running (e.g. what modules are executing)')
    parser.add_argument('-d', '--debug', dest='debug', default=False, action='store_true',
                        help='display debug level of information while running (e.g. messages being published)')
    parser.add_argument('-c', '--config', dest='config', default=str(bb.config_file), type=str,
                        help='config file to use (default: %(default)s)')
    parser.add_argument('-r', '--ssm-region', dest='ssm_region', default=None, type=str,
                        help=f'systems manager config region to use instead of native one; specify as aws region (e.g. \'{default_region}\')')
    parser.add_argument('-s', '--ssm', dest='ssm_config', default=False, action='store_true',
                        help='use systems manager for config; overrides -c config')
    parser.add_argument('-M', '--migrate-config', dest='ssm_migrate', default=False, action='store_true',
                        help='migrate INI configuration to systems manager. requires -s. will overwrite existing ssm!')
    parser.add_argument('-X', '--websocket-stub', dest='websocket_stub', default=False, action='store_true',
                        help='stub out the WebSocket handler for testing, just echo messages back')
    parser.add_argument('-l', '--cloudwatch-logs', dest='cloudwatch_logs', default=False, action='store_true',
                        help='use CloudWatch logging instead of STDOUT')
    parser.add_argument('-m', '--cloudwatch-metrics', dest='cloudwatch_metrics', default=False, action='store_true',
                        help='use CloudWatch for recording metrics')
    cli_args = parser.parse_args()

    # handle some command line arguments
    bb.logger.ll_verbose = cli_args.verbose
    bb.logger.ll_debug = cli_args.debug
    bb.ws_stub = cli_args.websocket_stub
    if cli_args.config:
        bb.config_file = cli_args.config
    if cli_args.ssm_config is True:
        if cli_args.ssm_region:
            bb.config_ssm = cli_args.ssm_region
            bb.logger.verbose(f"Using supplied SSM region: {bb.config_ssm}")
        else:
            bb.config_ssm = default_region
            bb.logger.verbose(f"Using default SSM region: {bb.config_ssm}")
    if cli_args.cloudwatch_metrics is True:
        bb.metrics_enabled = True

    # read configuration
    config = bb.read_config(cli_args)

    bb.server_running = True

    # set up the log and metrics handlers
    try:
        bb.logger.setup_loghandler(cli_args, config)
        bb.metrics.setup_metrics_handler(cli_args, config)
    except Exception as e:
        print(f"FATAL: Exception caught during logger/metrics setup: {e}")
        return 1

    # set up metrics reporting thread
    metrics_thread = None
    if bb.metrics.metrics_enabled is True:
        bb.logger.verbose("Establishing metrics reporter...")
        metrics_thread = Thread(name="metrics_reporter", target=bb.metrics.metrics_reporter)

    # Set up MQ service
    mq_thread = bb.setup_mqhandler(config)
    if mq_thread is None:
        bb.logger.error("Couldn't establish MQ handler - aborting run!")
        return 1

    # Set up interrupter thread
    bb.interrupt_secs = int(cli_args.run_secs)
    if bb.interrupt_secs == 0:
        bb.logger.verbose("Interrupter thread set up to run forever")
    else:
        bb.logger.verbose(f"Setting up interrupter to run for {bb.interrupt_secs} seconds...")
    bb.int_sleeper = Event()
    int_thread = Thread(name="Bridge_interrupter_thread", target=bb.interrupter, args=[bb.int_sleeper])
    int_thread.start()

    # set up signal handler (for Ctrl-C)
    import signal
    for sig in ('TERM', 'HUP', 'INT'):
        signal.signal(getattr(signal, 'SIG' + sig), bb.quit)

    # Set up WebSocket handler
    ws_thread = bb.setup_wshandler(config)

    successful_start = 0
    try:

        # start up various threads
        if metrics_thread is not None:
            metrics_thread.start()
        mq_thread.start()
        ws_thread.start()

        bb.logger.debug("Waiting for MQ and WebSocket threads to report that they're running...")
        for poll in range(60):
            if bb.ws_handler.ws_failed is True:
                bb.logger.error("ERROR: WebSocket connection failed; aborting bridge")
                bb.server_running = False
                bb.int_sleeper.set()
                successful_start = 1
                break

            if bb.mq_handler.mq_running is True and bb.ws_handler.ws_running is True and bb.ws_handler.ws_connected is True:
                bb.logger.debug("====== EVERYTHING IS RUNNING ======")
                break
            else:
                bb.mysleep(1)

        while bb.server_running is True:
            # if debug output, display dots as a visible pulse
            if bb.logger.ll_debug is True:
                print(".", end='')
                sys.stdout.flush()
            bb.mysleep(3, announce=False)

            # check that modules are still running
            if mq_thread.is_alive() is False or bb.mq_handler.mq_running is False:
                bb.logger.verbose("MQ thread detected as stopped, setting shutdown.")
                bb.server_running = False
                bb.int_sleeper.set()

            if ws_thread.is_alive() is False or bb.ws_handler.ws_running is False:
                bb.logger.verbose("WebSocket thread detected as stopped, setting shutdown.")
                bb.server_running = False
                bb.int_sleeper.set()

    except KeyboardInterrupt as ex:
        print("Keyboard interrupt caught; setting shutdown flag.")
        bb.server_running = False
        bb.int_sleeper.set()

    if mq_thread.is_alive() is True:
        bb.logger.debug("main(): Waiting for MQ thread to finish")
        mq_thread.join(JOIN_TIMEOUT)

    if ws_thread.is_alive() is True:
        bb.logger.debug("main(): Waiting for WebSocket thread to finish")
        ws_thread.join(JOIN_TIMEOUT)

    if metrics_thread is not None and metrics_thread.is_alive() is True:
        bb.logger.debug("main(): Waiting for metrics thread to finish")
        metrics_thread.join(JOIN_TIMEOUT)

    bb.logger.debug("main() Exiting!")
    return successful_start


if __name__ == "__main__":
    ret_code = main()
    if ret_code is not None:
        sys.exit(ret_code)
