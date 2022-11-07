#!/usr/bin/env python3
#
# metricshandler.py
#
# Metrics routines for the Mq2WsBridge.
#
# 2022-07-28, Brian J. Bernstein
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#

import os
import socket
import threading

import boto3
from botocore.exceptions import ClientError


class MetricsHandler:
    """
    Metric reporting handler and routines.
    """

    def __init__(self, sb_handle):
        """
        Initializes the MetricsHandler to a default state.
        """

        self.instance_id = None
        self.metrics_sleeper = None

        self.sb_handle = sb_handle  # reference to the main mq2wsbridge object
        self.logger = sb_handle.logger
        self.cw_metrics = None
        self.cw_metrics_namespace = None
        self.cw_region = None

        self.count_from_ws = 0
        self.count_to_ws = 0
        self.count_mq_connection_attempts = 0
        self.count_ws_connection_attempts = 0

        self.metrics_enabled = False
        self.metrics_resolution = None  # how many seconds to elapse between metric reporting

    def setup_metrics_handler(self, cli_args, config):
        """
        Set up the MetricsHandler object.
        Defines its settings based on the command line parser from main() and the handle to the
        initialized BridgeConfig object.
        :param cli_args: Command line parser handler.
        :param config: BridgeConfig handle.
        """
        # set up CloudWatch metrics
        if cli_args.cloudwatch_metrics is True and config.cw_region is not None:
            self.logger.verbose("CloudWatch metrics being established.")
            self.cw_region = config.cw_region

            self.cw_metrics_namespace = config.cw_metrics_namespace
            if self.cw_metrics_namespace is None:
                raise Exception("Namespace not defined in configuration for CloudWatch metrics")

            if config.cw_metrics_resolution is None:
                config.cw_metrics_resolution = 10
                self.logger.warn(
                    f"Metrics resolution not defined in configuration; using default {config.cw_metrics_resolution}")
            self.metrics_resolution = float(config.cw_metrics_resolution)

            self.instance_id = self.get_instanceid()

            self.cw_metrics = boto3.resource('cloudwatch', region_name=self.cw_region)
            self.metrics_enabled = True

            self.metrics_sleeper = threading.Event()

            self.logger.debug(f"CloudWatch metrics initialized")
        else:
            self.logger.verbose(
                f"Did not set up metrics handler (cwmt={cli_args.cloudwatch_metrics}, region={config.cw_region})")

    def get_instanceid(self):
        """
        Returns the EC2 instance ID this application is running.
        Will only query the session once, so subsequent calls will return the cached value.
        :return: EC2 instance ID.
        """
        if self.instance_id is None:
            ec2 = boto3.Session(region_name=self.cw_region).client('ec2')
            hostname = socket.gethostname()
            filters = [
                {
                    'Name': 'private-dns-name',
                    'Values': [hostname]
                }
            ]
            response = ec2.describe_instances(Filters=filters)["Reservations"]

            iid = 'local-' + socket.gethostname()
            if response is None or len(response) < 1:
                self.logger.warn(f"Not running on an EC2 instance; using '{iid}' for instance ID")
            else:
                iid = response[0]['Instances'][0]['InstanceId']
            return iid
        else:
            return self.instance_id

    def close(self):
        """
        Close the metrics handler and stop the thread that is reporting metrics to CloudWatch.
        """
        if self.metrics_sleeper is not None:
            self.metrics_sleeper.set()
        return

    def put_metric_data(self, namespace, name, value, unit):
        """
        Sends a single data value to CloudWatch for a metric. This metric is given
        a timestamp of the current UTC time.
        Dimensions for instance_id and pid are added to the metric.
        :param namespace: The namespace of the metric.
        :param name: The name of the metric.
        :param value: The value of the metric.
        :param unit: The unit of the metric.
        """

        try:
            metric = self.cw_metrics.Metric(namespace, name)
            metric.put_data(
                Namespace=namespace,
                MetricData=[{
                    'MetricName': name,
                    'Value': value,
                    'Unit': unit,
                    'Dimensions': [{
                        'Name': 'instance_id',
                        'Value': self.get_instanceid()
                    },
                        {
                            'Name': 'pid',
                            'Value': str(os.getpid())
                        }]
                }]
            )
            self.logger.debug(f"Put data for metric {namespace}.{name} with value {value} ({unit})")
        except ClientError:
            self.logger.error(f"Couldn't put data for metric {namespace}.{name}")
            raise

    def metrics_reporter(self):
        """
        Metrics reporting loop. Intended to be run as a thread that would be interrupted by the close() method.
        Runs forever and reports metrics to CloudWatch every several seconds (defined by metrics_resolution).
        """
        if self.metrics_enabled is False:
            return

        last_from_ws = 0
        last_to_ws = 0
        last_mq_connection_attempts = 0
        last_ws_connection_attempts = 0

        while self.sb_handle.server_running is True:
            self.metrics_sleeper.wait(timeout=self.metrics_resolution)

            self.logger.debug("Sending metrics to CloudWatch")

            try:
                # MQ connection attempts
                self.put_metric_data(self.cw_metrics_namespace, 'mq_connection_attempts_total',
                                     self.count_mq_connection_attempts, 'Count')
                self.put_metric_data(self.cw_metrics_namespace, 'mq_connection_attempts_recent',
                                     (self.count_mq_connection_attempts - last_mq_connection_attempts), 'Count')
                last_mq_connection_attempts = self.count_mq_connection_attempts

                # WebSocket connection attempts
                self.put_metric_data(self.cw_metrics_namespace, 'ws_connection_attempts_total',
                                     self.count_ws_connection_attempts, 'Count')
                self.put_metric_data(self.cw_metrics_namespace, 'ws_connection_attempts_recent',
                                     (self.count_ws_connection_attempts - last_ws_connection_attempts),
                                     'Count')
                last_ws_connection_attempts = self.count_ws_connection_attempts

                # messages to WebSocket
                self.put_metric_data(self.cw_metrics_namespace, 'to_ws_total', self.count_to_ws, 'Count')
                self.put_metric_data(self.cw_metrics_namespace, 'to_ws_persecond',
                                     ((self.count_to_ws - last_to_ws) / self.metrics_resolution), 'Count')
                last_to_ws = self.count_to_ws

                # messages from WebSocket
                self.put_metric_data(self.cw_metrics_namespace, 'from_ws_total', self.count_from_ws, 'Count')
                self.put_metric_data(self.cw_metrics_namespace, 'from_ws_persecond',
                                     ((self.count_from_ws - last_from_ws) / self.metrics_resolution), 'Count')
                last_from_ws = self.count_from_ws

            except Exception as e:
                self.logger.error(f"Caught exception trying to report metrics: {e}")


if __name__ == "__main__":
    print(f"{__file__} is not executable, it is just a library of functions for mq2wsbridge.py")
