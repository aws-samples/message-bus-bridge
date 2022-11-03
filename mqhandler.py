#!/usr/bin/env python3
#
# mqhandler.py
#
# MQ bus handling class.
#
# 2022-03-21, Brian J. Bernstein
#


import time

import amqpstorm


class MQHandler:

    def __init__(self):
        """
        Initializes up the MQ handler object.
        """

        self.mq_url = None

        self.connection = None
        self.channel = None

        self.mq_qname_to_ws = None
        self.mq_qname_from_ws = None
        self.mq_broker_id = None
        self.mq_port = None
        self.mq_userid = None
        self.mq_password = None
        self.mq_region = None

        self.ttl_from_ws = None  # number of seconds messages sent to MQ bus 'from websocket' TTL

        self.consumer_tag = None

        self.logger = None
        self.metrics = None

        # this is a reference to the method that should be called when a MQ message is received
        self.mq_to_ws_method = None

        self.mq_running = False

        # maximum number of retries on opens, sends, etc.
        self.max_retries = 5

    def setup_mqhandler(self, config):
        """
        Sets up the MQ handler based on configuration.
        :param config: Handle to bridge configuration object.
        """

        self.mq_broker_id = config.mq_broker_id
        self.mq_port = config.mq_port
        self.mq_userid = config.mq_userid
        self.mq_password = config.mq_password
        self.mq_region = config.mq_region

        self.mq_qname_to_ws = config.mq_qname_to_ws
        self.mq_qname_from_ws = config.mq_qname_from_ws
        if self.mq_qname_to_ws is None or self.mq_qname_from_ws is None:
            raise Exception("MQ queue names not specified in configuration!")

        if config.mq_ttl_from_ws is None:
            config.mq_ttl_from_ws = 300
            self.logger.warn(f"No TTL specified for MQ messages from WebSockets; using default {config.mq_ttl_from_ws}")
        self.ttl_from_ws = int(config.mq_ttl_from_ws)

        self.consumer_tag = config.mq_consumer_tag
        if self.consumer_tag is None:
            self.consumer_tag = "mqhandler"
            self.logger.warn(f"No consumer tag specified for MQ; using default {self.consumer_tag}")

        # TODO: this URL probably needs to be changed for non-AWS hosted MQ services.
        self.mq_url = f"amqps://{self.mq_userid}:{self.mq_password}@{self.mq_broker_id}.mq.{self.mq_region}.amazonaws.com:{self.mq_port}"

        return

    def create_connection(self):
        """
        Create a connection to the MQ broker.
        If the connection is made, then self.mq_running flag is set to True and the connection loop will remain
        durable until something sets the mq_running flag to False.
        """
        attempts = 0
        while self.mq_running is True:
            attempts += 1

            if self.metrics and self.metrics.metrics_enabled:
                self.metrics.count_mq_connection_attempts = attempts

            try:
                self.connection = amqpstorm.UriConnection(self.mq_url)
                break
            except amqpstorm.AMQPError as e:
                if self.max_retries and attempts > self.max_retries:
                    self.logger.error(f"Exceeded number of failed attempts to open MQ connection: {e}")
                    self.mq_running = False
                    break
                else:
                    self.logger.error(
                        f"Caught exception (retry {attempts}/{self.max_retries}) opening MQ connection: {e}")
                time.sleep(min(attempts * 2, 30))
            except KeyboardInterrupt:
                self.mq_running = False
                break

    def send_message_from_ws(self, msg):
        """
        Sends a message that originated from the WebSocket API to the MQ bus.
        This is intended only to be called by the WebSocket handler as it targets the MQ topic reserved for messages
        from the WebSocket API.
        :param msg: Message to send.
        :return: True if message was sent successfully, False if it failed.
        """
        return self.send_message('', self.mq_qname_from_ws, msg)

    def send_message(self, exchange, routing_key, body):
        """
        Send a message to the MQ bus.
        :param exchange: Exchange to publish the message to. CURRENTLY UNUSED.
        :param routing_key: Routing key to publish the message to.
        :param body: Message to publish.
        :return: True if message was sent successfully, False if it failed.
        """
        self.channel.queue.declare(routing_key, durable=True)

        properties = {
            'content_type': 'text/plain',
            'expiration': str(self.ttl_from_ws)
            # 'headers': {'key': 'value'}
        }

        message = amqpstorm.Message.create(self.channel, body, properties)

        retry_count = self.max_retries
        while retry_count > 0 and message is not None:
            if (not self.channel.consumer_tags or self.mq_running is False) and retry_count < self.max_retries:
                self.logger.error("MQ message not sent; handler is not running!")
                return False

            try:
                message.publish(routing_key)
                message = None  # by setting to None, we know it worked
                break
            except Exception as e:
                retry_count -= 1
                self.logger.error(
                    f"Exception caught during MQ message publish: {e}. Retry attempts left: {retry_count}")
                time.sleep(1)

        if message is None:
            self.logger.debug(f"Sent MQ message. Exchange: {exchange}, Routing Key: {routing_key}, Body: {body}")
            return True
        else:
            self.logger.error("MQ message not sent due to errors!")
            return False

    def __call__(self, message):
        """
        Callback for messages received on the MQ bus.
        Forwards the message to the mq_to_ws_method reference.
        :param message: Message received.
        :return: True if message was processed successfully, False if otherwise.
        """
        self.logger.debug("MQ received %r" % message.body)
        if not self.channel:
            self.logger.verbose("Couldn't consume MQ message since channel is closed")
            return False

        if self.mq_running is False:
            message.reject(requeue=True)
            self.logger.debug("Couldn't consume MQ message since server is not running (msg re-queued)")
            return False

        success = self.mq_to_ws_method(message.body)
        if success is True:
            message.ack()
        else:
            message.reject(requeue=True)
        self.logger.debug("MQ finished sending msg to WebSocket handler")
        return success

    def consume_messages(self, consume_queue):
        """
        Start the message consumer handling loop.
        This will run until the self.mq_running flag is set to False.
        :param consume_queue: Queue to consume messages from.
        """

        self.mq_running = True

        if not self.connection:
            self.create_connection()
        while self.mq_running is True:
            try:
                self.channel = self.connection.channel()
                self.channel.queue.declare(queue=consume_queue, durable=True)
                self.channel.basic.consume(self, consume_queue, no_ack=False, consumer_tag=self.consumer_tag)
                self.channel.start_consuming()
                if not self.channel.consumer_tags or self.mq_running is False:
                    self.channel.close()
            except amqpstorm.AMQPError as e:
                # only bother reporting and reconnecting if we're supposed to be running, otherwise
                # this could be due to shutdown
                if self.mq_running is True:
                    self.logger.error(f"MQ exception caught during consume loop: {e}")
                    self.create_connection()
            except KeyboardInterrupt:
                self.mq_running = False
                self.connection.close()
                break

    def run_mq_server(self):
        """
        Entry method to be called when the MQ message handler should be started up.
        This is a thin wrapper around consume_messages().
        :return:
        """
        # set up MQ consumer for to_ws queue
        self.consume_messages(self.mq_qname_to_ws)

        # if consume_messages returned, then we must be done
        self.mq_running = False
        self.logger.verbose("Exiting mq_server")

    def close(self):
        """
        Close MQ channels and connections to shut down the MQ handler.
        :return:
        """
        self.mq_running = False

        if self.channel is not None:
            self.logger.debug("Canceling consumer")
            self.channel.basic.cancel(self.consumer_tag)
            self.logger.debug("closing MQ channel")
            self.channel.close()

        if self.connection is not None:
            self.logger.debug("closing MQ connection")
            self.connection.close()


if __name__ == "__main__":
    print(f"{__file__} is not executable, it is just a library of functions for mq2wsbridge.py")
