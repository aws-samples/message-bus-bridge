#!/usr/bin/env python3
#
# loghandler.py
#
# Logging routines for the Mq2WsBridge.
#
# 2022-03-16, Brian J. Bernstein
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#

import threading
import time
from datetime import datetime
from threading import Lock

import boto3
from botocore.exceptions import ClientError


class LogHandler:
    """
    Log handling routines. Supports STDOUT and CloudWatch logging.
    """

    def __init__(self):
        """
        Initializes the LogHandler to a default state.
        Most things are just set to None, but notable is that only ERROR level logging is enabled
        by default.
        """
        self.ll_debug = False
        self.ll_verbose = False
        self.ll_warn = True
        self.ll_error = True

        self.cw_logger = None
        self.cw_seq_token = None
        self.cw_retention_days = None
        self.cw_region = None
        self.cw_log_group = None
        self.cw_log_stream = None
        self.cw_mutex = Lock()

    def output_log_message(self, level, msg):
        """
        Core log handling routine. Not intended to be called directly by users, but rather via
        the level-specific methods.

        :param level: Level that will be reported in the log message.
        :param msg: Log message.
        :return:
        """
        # if not using CloudWatch for logging
        if self.cw_logger is None:
            print(f"[{threading.current_thread().name} - {datetime.now()}] {level} - {msg}", flush=True)

        # otherwise we're using CloudWatch
        else:
            lmsg = f"[{threading.current_thread().name}] {level} - {msg}"

            levent = {
                'logGroupName': self.cw_log_group,
                'logStreamName': self.cw_log_stream,
                'logEvents': [
                    {
                        'timestamp': int(round(time.time() * 1000)),
                        'message': lmsg
                    }
                ]
            }

            if self.cw_logger is not None:
                self.cw_mutex.acquire(timeout=3000)
                try:
                    if self.cw_seq_token is not None:
                        levent['sequenceToken'] = self.cw_seq_token

                    log_response = self.cw_logger.put_log_events(**levent)
                    self.cw_seq_token = log_response['nextSequenceToken']
                finally:
                    self.cw_mutex.release()

        return

    def verbose(self, msg):
        """
        Log a message with the VERBOSE / INFO level.
        Print routine that is based on the self.ll_verbose bool.
        :param msg: verbose message.
        """
        if self.ll_verbose is True:
            self.output_log_message("INFO", msg)

    def warn(self, msg):
        """
        Log a message with the WARN level.
        Print routine that is based on the self.ll_warn bool.
        :param msg: warn message.
        """
        if self.ll_warn is True:
            self.output_log_message("WARN", msg)

    def debug(self, msg):
        """
        Log a message with the DEBUG level.
        Print routine that is based on the self.ll_debug bool.
        :param msg: Debug message.
        """
        if self.ll_debug is True:
            self.output_log_message("DEBUG", msg)

    def error(self, msg):
        """
        Log a message with the ERROR level.
        Print routine that is based on the self.ll_error bool.
        :param msg: Error message.
        """
        if self.ll_error is True:
            self.output_log_message("ERROR", msg)

    def setup_loghandler(self, cli_args, config):
        """
        Set up the LogHandler class.
        Defines its settings based on the command line parser from main() and the handle to the
        initialized BridgeConfig object.
        :param cli_args: Command line parser handler.
        :param config: BridgeConfig handle.
        """
        # set up CloudWatch
        if cli_args.cloudwatch_logs is True and config.cw_region is not None:
            self.verbose("Logs are being redirected to CloudWatch; STDOUT disabled.")

            self.cw_region = config.cw_region
            self.cw_log_group = config.cw_log_group
            self.cw_log_stream = config.cw_log_stream
            if self.cw_region is None or self.cw_log_group is None or self.cw_log_stream is None:
                raise Exception("CloudWatch region, log stream, and/or log group not specified!")

            self.cw_retention_days = int(config.cw_retention_days)
            if self.cw_retention_days is None:
                self.cw_retention_days = 30
                self.logger.verbose(
                    f"No CloudWatch retention days specified; using default {self.cw_retention_days}")

            self.cw_logger = boto3.client('logs', region_name=self.cw_region)

            # Back end Log Group
            try:
                response = self.cw_logger.create_log_group(
                    logGroupName=self.cw_log_group,
                    tags={
                        'Type': 'Back end',
                        'Frequency': '30 seconds',
                        'Environment': 'Production',
                        'RetentionPeriod': str(self.cw_retention_days)
                    }
                )
            except ClientError as e:
                if e.response['Error']['Code'] != "ResourceAlreadyExistsException":
                    print(f"Got unexpected exception '{e.response['Error']['Code']}' creating log group: {e}")
                pass

            try:
                response = self.cw_logger.create_log_stream(
                    logGroupName=self.cw_log_group,
                    logStreamName=self.cw_log_stream
                )
            except ClientError as e:
                if e.response['Error']['Code'] != "ResourceAlreadyExistsException":
                    print(f"Got unexpected exception '{e.response['Error']['Code']}' creating log stream: {e}")
                pass

            try:
                response = self.cw_logger.describe_log_streams(
                    logGroupName=self.cw_log_group,
                    orderBy='LastEventTime'
                )

                if 'logStreams' in response and 'uploadSequenceToken' in response['logStreams'][0]:
                    self.cw_seq_token = response['logStreams'][0]['uploadSequenceToken']
            except ClientError as e:
                if e.response['Error']['Code'] != "ResourceNotFoundException":
                    print(f"Got unexpected exception '{e.response['Error']['Code']}' fetching log token: {e}")
                pass


if __name__ == "__main__":
    print(f"{__file__} is not executable, it is just a library of functions for mq2wsbridge.py")
