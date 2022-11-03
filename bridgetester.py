#!/usr/bin/env python3
#
# bridgetester.py
#
# Test script that publishes messages to MQ and tells of any responses, and provides the timing of the response
#
# 2022-03-25, Brian J. Bernstein
#
# Copyright 2022 Amazon.com and its affiliates; all rights reserved.
# This file is AWS Content and may not be duplicated or distributed without permission.
#

import argparse
import configparser
import curses
import datetime
import logging
import os
import sys
import time
from curses import wrapper
from datetime import datetime
from threading import Thread

import amqpstorm

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()


class MsgRecord():
    """
    Message record structure. Used for keeping track of messages we sent / received and data around that.
    """

    def __init__(self):
        self.msg = None
        self.time_send = None
        self.time_recv = None
        self.time_elapsed = None


class BridgeTester():
    """
    MQ to WebSocket bridge testing class.
    """

    def __init__(self, mq_broker_id, mq_user_id, mq_password, mq_region, max_retries=None, queuename_to_ws=None,
                 queuename_from_ws=None):
        self.status_rows = 3
        self.msg_records = []
        self.lowest_unreconciled = 0  # lowest message num that is unreconciled at last check
        self.scr_handle = None

        self.mq_broker_id = mq_broker_id
        self.mq_user_id = mq_user_id
        self.mq_password = mq_password
        self.mq_region = mq_region
        self.mq_ttl = 300 * 1000

        self.queuename_to_ws = queuename_to_ws
        self.queuename_from_ws = queuename_from_ws

        self.consumer_tag = "bridgetester"
        self.running = True
        self.max_retries = max_retries
        self.connection = None
        self.channel = None

        self.unique_id = None
        self.exclusive = False
        self.graph_mode = False
        self.end_delay = 10
        self.focused = False

    def find_msg(self, msg):
        """
        Searches the list of MsgRecord objects for the one that matches the provided message.
        :param msg: Message to search for in the list of MsgRecord objects.
        :return: Reference to the MsgRecord that matches, or None if not found.
        """

        for x in range(self.lowest_unreconciled, len(self.msg_records)):
            m = self.msg_records[x]
            if m.msg == msg:
                return m
        return None

    def display_graph(self):
        if self.msg_records is None:
            return

        msgs_total = len(self.msg_records)

        # figure out how many rows the table can be
        [mxy, mxx] = self.scr_handle.getmaxyx()
        start_from_msg = 0
        start_row = 2

        max_rows = mxy - start_row - 1
        num_rows = (msgs_total / mxx)
        if num_rows != float(int(num_rows)):
            num_rows = int(num_rows) + 1

        if num_rows > max_rows:
            start_from_msg = (num_rows - max_rows) * mxx
            self.scr_handle.addstr((max_rows + start_row - 1), 0, ' ')
            self.scr_handle.clrtoeol()

        # display graph
        for x in range(int(start_from_msg), msgs_total):
            pxy = int((x - start_from_msg) / mxx) + start_row
            pxx = int(((x - start_from_msg) - ((pxy - start_row) * mxx)))

            m = self.msg_records[x]

            pt = ' '
            if m.time_recv is not None:
                pt = 'o'
            elif m.time_send is not None:
                pt = '.'

            self.scr_handle.addstr(pxy, pxx, pt)

        self.scr_handle.addstr(0, 0, f"[Msgs: {self.reconciled_msgs_count()} of {msgs_total}]")
        self.scr_handle.refresh()
        return

    def display_msgs(self):
        if self.msg_records is None:
            return

        msgs_total = len(self.msg_records)

        # figure out how many rows the table can be
        [mxy, mxx] = self.scr_handle.getmaxyx()
        start_msg = 0
        max_table_size = mxy - self.status_rows - 1
        if msgs_total > max_table_size:
            start_msg = msgs_total - max_table_size

        # set up table for display
        col_msg = 0
        col_sent = col_msg + 30
        col_recv = col_sent + 15
        col_elapsed = col_recv + 15
        col_counts = col_elapsed + 15

        self.scr_handle.addstr(0, col_msg, "Message")
        self.scr_handle.addstr(0, col_sent, "Sent At")
        self.scr_handle.addstr(0, col_recv, "Received At")
        self.scr_handle.addstr(0, col_elapsed, "Elapsed")

        ypos = 0
        for x in range(start_msg, msgs_total):
            m = self.msg_records[x]
            ypos += 1
            self.scr_handle.addstr(ypos, col_msg, m.msg)
            self.scr_handle.clrtoeol()
            self.scr_handle.addstr(ypos, col_sent, m.time_send.strftime("%H:%M:%S"))
            if m.time_recv is not None:
                self.scr_handle.addstr(ypos, col_recv, m.time_recv.strftime("%H:%M:%S"))
            else:
                self.scr_handle.addstr(ypos, col_recv, "---")
            if m.time_elapsed is not None:
                self.scr_handle.addstr(ypos, col_elapsed, m.time_elapsed.strftime("%H:%M:%S.%f"))
            else:
                self.scr_handle.addstr(ypos, col_elapsed, "---")

        self.scr_handle.addstr(0, col_counts, f"[Msgs: {self.reconciled_msgs_count()} of {msgs_total}]")
        self.scr_handle.refresh()
        return

    def reconciled_msgs_count(self):
        """
        Returns the number of reconciled messages, i.e. messages which we've sent to the server and received a response.
        As a side, it also updates the self.lowest_unreconciled variable.
        :return: Number of reconciled messages.
        """
        reconciled = -1
        if self.msg_records is not None:
            reconciled = self.lowest_unreconciled
            found_unreconciled = False
            for x in range(self.lowest_unreconciled, len(self.msg_records)):
                m = self.msg_records[x]
                if m.time_elapsed is None:
                    found_unreconciled = True
                else:
                    reconciled += 1
                    if self.lowest_unreconciled == x and found_unreconciled is False:
                        self.lowest_unreconciled = x + 1
        return reconciled

    def report_unreconciled_msgs(self, cli_args):
        """
        Reports all the unreconciled messages to the report_file
        :param cli_args: Command line arguments where report_file is retrieved from.
        """
        if self.msg_records is not None:
            with open(cli_args.report_file, 'w') as out:
                for x in range(0, len(self.msg_records)):
                    m = self.msg_records[x]
                    if m.time_elapsed is None:
                        out.write(f"{m.msg}\n")

    def statusmsg(self, msg, force=False):
        """
        Displays status message. If in visual mode, this is at the bottom of the window, otherwise it is just STDOUT.
        In visual mode, most messages won't be displayed because of potential threading race conditions could
        corrupt the screen slightly. So only messages that are 'forced' in visual mode will actually be displayed.
        :param msg: Message to display.
        :param force: Force the message to be displayed in visual mode.
        :return:
        """
        if cli_args.visual is False and cli_args.graph_mode is False:
            print(msg)
        elif force is True:
            [mxy, mxx] = self.scr_handle.getmaxyx()
            status_ypos = mxy - self.status_rows
            if mxx < 11:
                status_ypos = 10

            self.scr_handle.addstr(status_ypos, 0, ">> " + msg)
            self.scr_handle.clrtobot()
            self.scr_handle.refresh()
        return

    def create_connection(self):
        """
        Create a connection.
        :return:
        """
        attempts = 0
        while self.running is True:
            attempts += 1
            try:
                port = 5671
                url = f"amqps://{self.mq_user_id}:{self.mq_password}@{self.mq_broker_id}.mq.{self.mq_region}.amazonaws.com:{port}"
                self.statusmsg(f"Creating MQ connection", force=False)
                self.connection = amqpstorm.UriConnection(url)
                break
            except amqpstorm.exception.AMQPConnectionError as why:
                print(f"Exception trying to connect to MQ server; aborting!  (does MQ host exist?)")
                self.running = False
                break
            except amqpstorm.AMQPError as why:
                LOGGER.exception(why)
                if self.max_retries and attempts > self.max_retries:
                    break
                time.sleep(min(attempts * 2, 30))
            except KeyboardInterrupt:
                self.running = False
                break

    def send_message(self, body):
        if self.running is False:
            return

        self.channel.queue.declare(self.queuename_to_ws, durable=True)
        properties = {
            'content_type': 'text/plain',
            'expiration': str(self.mq_ttl)
            # 'headers': {'key': 'value'}
        }
        message = amqpstorm.Message.create(self.channel, body, properties)
        message.publish(self.queuename_to_ws)
        self.statusmsg(f"Sent message. Body: {body}")

    def close(self):
        if self.channel:
            self.channel.basic.cancel(self.consumer_tag)

        self.running = False

        if self.channel:
            self.channel.close()

        if self.connection:
            self.connection.close()

    def consume_messages(self):
        """
        Start the consuming incoming messages from the bridge.
        """

        if not self.connection:
            self.create_connection()
        while self.running is True:
            try:
                self.channel = self.connection.channel()
                self.channel.queue.declare(queue=self.queuename_from_ws, durable=True)
                self.channel.basic.consume(self, self.queuename_from_ws, no_ack=False, consumer_tag=self.consumer_tag)
                self.channel.start_consuming()
                if not self.channel.consumer_tags or self.running is False:
                    self.channel.close()
            except amqpstorm.AMQPError as why:
                LOGGER.exception(why)
                self.create_connection()
            except KeyboardInterrupt:
                self.running = False
                self.connection.close()
                break

    def __call__(self, message):
        # just ignore the message if we're shutting down
        if self.running is False:
            return

        if self.focused is False:
            self.statusmsg(f"{datetime.now()}<<< Received {message.body} tag#{message.delivery_tag}")

        msg = self.find_msg(message.body)
        if msg is None:
            if self.focused is False:
                self.statusmsg(f"Can't report turnaround time for unknown message: '{message.body}'", force=False)

            if self.exclusive is True:
                message.ack()
            else:
                message.reject(requeue=True)
        else:
            msg.time_recv = datetime.now()
            msg.time_elapsed = datetime.utcfromtimestamp((msg.time_recv - msg.time_send).total_seconds())
            self.statusmsg(f"Turnaround time for '{message.body}': {msg.time_elapsed.strftime('%H:%M:%S.%f')}")
            message.ack()


def main(scr, cli_args):
    config = configparser.ConfigParser()
    config.read(cli_args.config_file)
    bt = None
    try:
        bt = BridgeTester(
            mq_broker_id=config['aws_mq']['mq_broker_id'],
            mq_user_id=config['aws_mq']['mq_user_id'],
            mq_password=config['aws_mq']['mq_password'],
            mq_region=config['aws_mq']['mq_region'],
            max_retries=3,
            queuename_to_ws=config['aws_mq']['mq_qname_to_ws'],
            queuename_from_ws=config['aws_mq']['mq_qname_from_ws']
        )
    except:
        print("Exception caught while attempting to open MQ; is broker reachable?")
        return False

    bt.scr_handle = scr
    bt.unique_id = os.getpid()
    bt.exclusive = cli_args.exclusive
    bt.graph_mode = cli_args.graph_mode
    bt.end_delay = cli_args.end_delay
    bt.focused = cli_args.focused

    # Consume multiple messages in an event loop.
    recv_thread = Thread(name="recv_thread", target=bt.consume_messages)
    recv_thread.start()

    # Send a message to the queue.
    time.sleep(1)
    for x in range(1, cli_args.num_msgs + 1):
        if bt.running is False:
            break

        if cli_args.graph_mode is True:
            bt.display_graph()
        elif cli_args.visual is True:
            bt.display_msgs()

        bt.statusmsg(f"{datetime.now()}>>> Sending message #{x} to MQ")
        time_start = datetime.now()
        ts = time_start.strftime("%M%S")
        msg = f"Test msg #{x} ({bt.unique_id}.{ts})"

        msgRec = MsgRecord()
        msgRec.msg = msg
        msgRec.time_send = time_start
        bt.msg_records.append(msgRec)

        bt.send_message(body=msg)
        time_end = datetime.now()
        sys.stdout.flush()
        if cli_args.msg_delay > 0:
            time.sleep(cli_args.msg_delay)

    # if there are unreconciled messages, then wait a bit before exiting
    done_counter = bt.end_delay
    while done_counter > 0:
        if cli_args.visual is True:
            bt.display_msgs()
        elif cli_args.graph_mode is True:
            bt.display_graph()
        rm = bt.reconciled_msgs_count()
        if rm == -1 or rm == len(bt.msg_records):
            done_counter = 0
        else:
            sleep_time = 0.1
            time.sleep(sleep_time)
            done_counter -= sleep_time

    # Close connections.
    bt.statusmsg("All done testing, closing things out...", force=True)
    bt.close()

    if cli_args.report_file is not None:
        print(f"Number of messages reconciled: {bt.reconciled_msgs_count()} of {cli_args.num_msgs}")
        bt.report_unreconciled_msgs(cli_args)

    if cli_args.visual is True:
        bt.statusmsg("Program finished - press any key to exit.", force=True)
        scr.getch()
        curses.endwin()

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='MQ to WebSocket Bridge Tester.')
    parser.add_argument('-v', '--visual', dest='visual', default=False, action='store_true',
                        help='displays a curses-based table of when messages are sent/received and elapsed time')
    parser.add_argument('-n', '--number', dest='num_msgs', default=int(8), type=int,
                        help='number of test messages to be sent before quitting (default: %(default)i)')
    parser.add_argument('-d', '--delay', dest='msg_delay', default=float(1.0), type=float,
                        help='number of seconds to delay between messages (default: %(default)i)')
    parser.add_argument('-x', '--exclusive', dest='exclusive', default=False, action='store_true',
                        help='exclusive mode, i.e. we will consume any message we receive regardless if it was for us (bad for production environments, but good for clearing out stale test run messages)')
    parser.add_argument('-e', '--end-delay', dest='end_delay', default=int(30), type=int,
                        help='number of seconds to delay at end if not all messages have been reconciled (default: %(default)i)')
    parser.add_argument('-g', '--graph', dest='graph_mode', default=False, action='store_true',
                        help='displays a visual of what messages have been reconciled, but does not provide details')
    parser.add_argument('-r', '--report', dest='report_file', type=str,
                        help='generate file report which messages were non-reconciled at the end of the run')
    parser.add_argument('-f', '--focused', dest='focused', default=False, action='store_true',
                        help='output is focused only on message we sent; do not bother reporting others we may reject ' \
                             '(only applies to non-visual/graph modes)')
    parser.add_argument('-c', '--config', dest='config_file', default=str('mq2wsbridge.ini'), type=str,
                        help='config file to use (default: %(default)s)')
    cli_args = parser.parse_args()

    scr = None
    success = False
    if cli_args.visual is True or cli_args.graph_mode is True:
        success = wrapper(main, cli_args)
    else:
        success = main(None, cli_args)

    if success is False:
        print("Bridge Tester exited with errors.")
