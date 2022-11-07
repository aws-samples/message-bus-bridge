#!/usr/bin/env python3
#
# confighandler.py
#
# Configuration handler for the mq2wsbridge service.
#
# 2022-04-12, Brian J. Bernstein
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#


import configparser

import boto3


class BridgeConfig:

    def __init__(self, config_file, ssm_region):
        """
        Initializes the config class. Does not read configuration, you need to do that with read_config().
        :param config_file: Configuration INI file that will be used.
        :param ssm_region: AWS region where AWS Systems Manager will be queried.
        """

        self.ssm_base = "/mq2wsbridge"

        self.config_file = config_file
        self.config_ssm = ssm_region

        self.mq_broker_id = None
        self.mq_port = None
        self.mq_userid = None
        self.mq_password = None
        self.mq_region = None
        self.mq_qname_to_ws = None
        self.mq_qname_from_ws = None
        self.mq_ttl_from_ws = None
        self.mq_consumer_tag = None

        self.cw_region = None
        self.cw_log_group = None
        self.cw_log_stream = None
        self.cw_retention_days = None
        self.cw_metrics_namespace = None
        self.cw_metrics_resolution = None

        self.ws_api_host = None
        self.ws_api_uri = None
        self.ws_client_id = None
        self.ws_client_password = None
        self.ws_ping_interval = None
        self.ws_max_connect_attempts = None
        self.ws_attempt_window_secs = None

    def read_config(self):
        """
        Read configuration. If the SSM was defined in class init, then it will read from AWS Systems Manager.
        Otherwise, if the config file INI was defined in class init, then that will be read.
        Else, there will be an error thrown.
        """
        if self.config_ssm is not None:
            # read config from systems manager
            self.read_config_ssm()
        elif self.config_file is not None:
            # read config from INI file
            self.read_config_ini()
        else:
            raise RuntimeError("No configuration mechanism specified; cannot bring up bridge")

    def read_config_ini(self):
        """
        Reads configuration from the INI config file defined at class construction.
        """
        # read configuration from INI file
        config = configparser.ConfigParser()
        config.read(self.config_file)

        # Read MQ service configs
        self.mq_broker_id = config['aws_mq']['mq_broker_id']
        self.mq_port = config['aws_mq']['mq_port']
        self.mq_userid = config['aws_mq']['mq_user_id']
        self.mq_password = config['aws_mq']['mq_password']
        self.mq_region = config['aws_mq']['mq_region']
        self.mq_qname_to_ws = config['aws_mq']['mq_qname_to_ws']
        self.mq_qname_from_ws = config['aws_mq']['mq_qname_from_ws']
        self.mq_ttl_from_ws = config['aws_mq']['mq_ttl_from_ws']
        self.mq_consumer_tag = config['aws_mq']['mq_consumer_tag']

        # Read WebSocket service configs
        self.ws_api_host = config['ws_api']['api_host']
        self.ws_api_uri = config['ws_api']['api_uri']
        self.ws_client_id = config['ws_api']['client_id']
        self.ws_client_password = config['ws_api']['client_secret']
        self.ws_ping_interval = config['ws_api']['ws_ping_interval']
        self.ws_max_connect_attempts = config['ws_api']['ws_max_connect_attempts']
        self.ws_attempt_window_secs = config['ws_api']['ws_attempt_window_secs']

        # Read CloudWatch configs
        self.cw_region = config['aws_cloudwatch']['cw_region']
        self.cw_log_group = config['aws_cloudwatch']['cw_log_group']
        self.cw_log_stream = config['aws_cloudwatch']['cw_log_stream']
        self.cw_retention_days = config['aws_cloudwatch']['cw_retention_days']
        self.cw_metrics_namespace = config['aws_cloudwatch']['cw_metrics_namespace']
        self.cw_metrics_resolution = config['aws_cloudwatch']['cw_metrics_resolution']
        return

    def convert_ini_to_ssm(self):
        """
        Copies the INI config file settings into AWS Systems Manager.
        This is basically a convenience to quickly set up the SSM config store by using the INI as a source
        instead of working with the SSM directly.
        Intended to be called as a standalone operation and not part of a running system.
        :return: True if the operation worked, False if it failed.
        """

        def write_parameter(issm, pname, pval):
            pname_full = self.ssm_base + pname
            issm.put_parameter(Name=pname_full, Description='migrated from INI', Value=pval,
                               Type='SecureString', KeyId='alias/aws/ssm', Overwrite=True)

        if self.config_ssm is None:
            print("Can't migrate as SSM region was not specified!")
            return False

        self.read_config_ini()
        ssm = boto3.client('ssm', self.config_ssm)

        # Read MQ service configs
        write_parameter(ssm, '/aws_mq/mq_broker_id', self.mq_broker_id)
        write_parameter(ssm, '/aws_mq/mq_port', self.mq_port)
        write_parameter(ssm, '/aws_mq/mq_userid', self.mq_userid)
        write_parameter(ssm, '/aws_mq/mq_password', self.mq_password)
        write_parameter(ssm, '/aws_mq/mq_region', self.mq_region)
        write_parameter(ssm, '/aws_mq/mq_qname_to_ws', self.mq_qname_to_ws)
        write_parameter(ssm, '/aws_mq/mq_qname_from_ws', self.mq_qname_from_ws)
        write_parameter(ssm, '/aws_mq/mq_ttl_from_ws', self.mq_ttl_from_ws)
        write_parameter(ssm, '/aws_mq/mq_consumer_tag', self.mq_consumer_tag)

        # Read CloudWatch configs
        write_parameter(ssm, '/aws_cloudwatch/cw_region', self.cw_region)
        write_parameter(ssm, '/aws_cloudwatch/cw_log_group', self.cw_log_group)
        write_parameter(ssm, '/aws_cloudwatch/cw_log_stream', self.cw_log_stream)
        write_parameter(ssm, '/aws_cloudwatch/cw_retention_days', self.cw_retention_days)
        write_parameter(ssm, '/aws_cloudwatch/cw_metrics_namespace', self.cw_metrics_namespace)
        write_parameter(ssm, '/aws_cloudwatch/cw_metrics_resolution', self.cw_metrics_resolution)

        # Read WebSocket service configs
        write_parameter(ssm, '/ws_api/ws_api_host', self.ws_api_host)
        write_parameter(ssm, '/ws_api/ws_api_uri', self.ws_api_uri)
        write_parameter(ssm, '/ws_api/ws_client_id', self.ws_client_id)
        write_parameter(ssm, '/ws_api/ws_client_password', self.ws_client_password)
        write_parameter(ssm, '/ws_api/ws_ping_interval', self.ws_ping_interval)
        write_parameter(ssm, '/ws_api/ws_max_connect_attempts', self.ws_max_connect_attempts)
        write_parameter(ssm, '/ws_api/ws_attempt_window_secs', self.ws_attempt_window_secs)
        return True

    def read_config_ssm(self):
        """
        Reads configuration from the AWS Systems Manager defined at class construction.
        """

        def read_parameter(ssm, pname):
            pname_full = self.ssm_base + pname
            return ssm.get_parameter(Name=pname_full, WithDecryption=True)['Parameter']['Value']

        # read configuration from Systems Manager
        ssm = boto3.client('ssm', self.config_ssm)

        # Read MQ service configs
        self.mq_broker_id = read_parameter(ssm, '/aws_mq/mq_broker_id')
        self.mq_port = read_parameter(ssm, '/aws_mq/mq_port')
        self.mq_userid = read_parameter(ssm, '/aws_mq/mq_userid')
        self.mq_password = read_parameter(ssm, '/aws_mq/mq_password')
        self.mq_region = read_parameter(ssm, '/aws_mq/mq_region')
        self.mq_qname_to_ws = read_parameter(ssm, '/aws_mq/mq_qname_to_ws')
        self.mq_qname_from_ws = read_parameter(ssm, '/aws_mq/mq_qname_from_ws')
        self.mq_ttl_from_ws = read_parameter(ssm, '/aws_mq/mq_ttl_from_ws')
        self.mq_consumer_tag = read_parameter(ssm, '/aws_mq/mq_consumer_tag')

        # Read CloudWatch configs
        self.cw_region = read_parameter(ssm, '/aws_cloudwatch/cw_region')
        self.cw_log_group = read_parameter(ssm, '/aws_cloudwatch/cw_log_group')
        self.cw_log_stream = read_parameter(ssm, '/aws_cloudwatch/cw_log_stream')
        self.cw_retention_days = read_parameter(ssm, '/aws_cloudwatch/cw_retention_days')
        self.cw_metrics_namespace = read_parameter(ssm, '/aws_cloudwatch/cw_metrics_namespace')
        self.cw_metrics_resolution = read_parameter(ssm, '/aws_cloudwatch/cw_metrics_resolution')

        # Read WebSocket service configs
        self.ws_api_host = read_parameter(ssm, '/ws_api/ws_api_host')
        self.ws_api_uri = read_parameter(ssm, '/ws_api/ws_api_uri')
        self.ws_client_id = read_parameter(ssm, '/ws_api/ws_client_id')
        self.ws_client_password = read_parameter(ssm, '/ws_api/ws_client_password')
        self.ws_ping_interval = read_parameter(ssm, '/ws_api/ws_ping_interval')
        self.ws_max_connect_attempts = read_parameter(ssm, '/ws_api/ws_max_connect_attempts')
        self.ws_attempt_window_secs = read_parameter(ssm, '/ws_api/ws_attempt_window_secs')

        return
