#!/usr/bin/env python3
#
# wshandler.py
#
# WebSockets api handling code
#
# 2022-03-18, Brian J. Bernstein
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#


import base64
import binascii
import hashlib
import hmac
import json
import time
import urllib.parse as urlencodelib
import uuid

import websocket as ws

# how long a send_message STUB call will wait before echoing it back to the on_message callback.
# set to 0 if you want maximum performance or some number of seconds (float) to delay if you want
# to simulate more realistic behavior
STUB_SEND_DELAY = 0


class WSHandler:
    """
    WebSocket handling class.
    """

    def __init__(self):
        self.ws_failed: str = None

        # this is if the websocket handler is connected
        self.ws_connected: bool = False

        # this is if the websocket handler is supposed to be running
        self.ws_running: bool = False
        self.ws_handle = None
        self.ws_api_host = None
        self.ws_api_uri = None
        self.ws_credentials = None
        self.ws_ping_interval = None
        self.ws_max_connect_attempts = None
        self.ws_attempt_window_secs = None

        self.logger = None
        self.metrics = None

        # this is a reference to the method that should be called when a websocket message is received
        self.on_message_handler = None

    def generate_url(self, path, method, credentials, uri, jwt_params={}, header_params={}, query_params={}):
        client_id = credentials["clientId"]
        client_secret = credentials["clientSecret"]
        query_params['jwt'] = self.generate_jwt(client_id, binascii.unhexlify(client_secret), path, uri, method,
                                                jwt_params,
                                                header_params)
        return uri + path + '?' + urlencodelib.urlencode(query_params)

    def generate_websocket_url(self, path, method, credentials, uri, jwt_params={}, header_params={}, query_params={}):
        websocket_uri = uri
        # websocket_uri = uri.replace('http', 'ws', 1)

        websocket_jwt_params = dict({
                                        'connection_expiry': int(round(time.time())) + 300
                                    }.items() | jwt_params.items())

        return self.generate_url(path, method, credentials, websocket_uri, websocket_jwt_params, header_params,
                                 query_params)

    def generate_jwt(self, key_id, key_secret, path, uri, method, jwt_params={}, header_params={}):
        header = self.generate_header(header_params)
        payload = self.generate_payload(key_id, path, uri, method, jwt_params)
        jwt_header = header + "." + payload
        signature = self.generate_signature(jwt_header, key_secret)
        return jwt_header + "." + signature

    def b64encode(self, string):
        if type(string) != type(b''):
            string = string.encode()
        return base64.urlsafe_b64encode(string).replace("=".encode(), "".encode()).decode()

    def generate_header(self, params):
        algo = {
            "alg": "HS256",
            "typ": "JWT"
        }
        headers = dict(algo.items() | params.items())
        return self.b64encode(json.dumps(headers))
        # return base64.urlsafe_b64encode(json.dumps(headers)).replace("=", "")

    def generate_payload(self, key_id, path, uri, method, payload_params={}):
        current_time = int(round(time.time()))
        jwt_payload = {
            "iss": key_id,
            "kid": key_id,
            "exp": current_time + 300,
            "nbf": current_time - 60,
            "iat": current_time - 60,
            "region": "ny",
            "method": method,
            "path": path,
            "host": uri,
            "client_id": key_id,
            "nonce": str(uuid.uuid4())
        }
        payload = dict(jwt_payload.items() | payload_params.items())
        return self.b64encode(json.dumps(payload))
        # return base64.urlsafe_b64encode(json.dumps(payload)).replace("=", "")

    def generate_signature(self, payload, key_secret):
        hs256 = hmac.new(key_secret, payload.encode(), hashlib.sha256)
        digest = hs256.digest()
        return self.b64encode(digest)
        # return base64.urlsafe_b64encode(digest).replace("=", "")

    def on_open(self, ws):
        """
        Callback when the websocket is opened.
        :param ws: websocket handle.
        """
        self.logger.verbose("WebSocket opened")
        self.ws_connected = True
        # print("CONNECTION OPENED")

    def on_message(self, ws, message):
        """
        Callback when a message is received from the websocket.
        :param ws: websocket handle.
        :param message: Received WebSocket message.
        """
        self.logger.debug(f"WebSocket: received message: {message}")
        if self.on_message_handler is None:
            self.logger.error(f"Received message but no handler to consume: {message}")
        else:
            self.logger.debug("WebSocket: sending msg to MQ handler")
            if self.metrics and self.metrics.metrics_enabled:
                self.metrics.count_from_ws += 1
            self.on_message_handler(message)

    def on_close(self, ws, code, msg):
        """
        Callback when the websocket is closed.
        :param ws: websocket handle.
        :param code:
        :param msg:
        :return:
        """
        if self.ws_running is True:
            self.logger.warn("websocket on_close called when ws_running is TRUE?")
        # self.ws_running = False
        # print("CONNECTION CLOSED")
        self.ws_connected = False

        self.logger.verbose(f"on_close code:{code} msg:{msg}")
        if code is None and msg is None:
            self.ws_failed = "websocket appears to have failed to connect (unidentified by library)"
            self.logger.warn(self.ws_failed)

    def on_error(self, ws, ex):
        # print("CONNECTION ERRORED")
        self.logger.error(f"Received a websocket error: {ex}")

    def on_data(self, ws, data, datatype, flag):
        self.logger.debug(f"ON_DATA: data={data}, type={datatype}, flag={flag}")

    def on_cont_message(self, ws, data, flag):
        self.logger.debug(f"ON_CONT_MESSAGE: data={data}, flag={flag}")

    def run_ws_server(self):
        self.ws_running = True
        last_attempt_time = 0

        attempt = 0
        while attempt < self.ws_max_connect_attempts:
            attempt += 1
            last_attempt_time = time.time()

            if self.ws_running is False:
                self.logger.verbose("Aborting attempt to start websocket due to ws_running=False")
                return
            else:
                self.logger.verbose(f"Attempt #{attempt} of {self.ws_max_connect_attempts} to open websocket")

            if self.metrics and self.metrics.metrics_enabled:
                self.metrics.count_ws_connection_attempts = attempt

            try:
                uri = self.generate_websocket_url(self.ws_api_uri, 'GET', self.ws_credentials, self.ws_api_host)
                self.ws_handle = ws.WebSocketApp(uri,
                                                 on_open=self.on_open,
                                                 on_message=self.on_message,
                                                 on_error=self.on_error,
                                                 on_close=self.on_close,
                                                 on_cont_message=self.on_cont_message,
                                                 on_data=self.on_data)
            except Exception as e:
                self.logger.warn(f"Exception caught while trying to establish WebSocket connection: {e}")
                continue

            try:
                # teardown is 'false' is connection closed or caught ctrl-c; true otherwise.
                teardown = self.ws_handle.run_forever(ping_interval=int(self.ws_ping_interval),
                                                      skip_utf8_validation=True)

                # if we're still supposed to be running and enough time has passed, then reset the connection
                # attempt counter (we'll loop again and retry)
                if (time.time() - last_attempt_time) > self.ws_attempt_window_secs:
                    attempt = 0

                if teardown is True:
                    self.logger.error("WebSocket exited with an exception? (teardown=True)")
                if self.ws_running is True:
                    self.logger.warn("Unexpected exit of websockets forever loop? Supposed to be running!")
            except KeyboardInterrupt:
                self.logger.warn("Caught keyboard interrupt from WebSocket handler; setting shutdown")
                self.close()
            except Exception as e:
                self.logger.error(f"Exception caught from run_forever! {e}")
                raise e

        if attempt >= self.ws_max_connect_attempts:
            self.ws_failed = "Exiting WebSocket forever loop because number of connect attempts exceeded"
            self.logger.error(self.ws_failed)

    def run_ws_server_stub(self):
        self.ws_running = True
        self.ws_connected = True
        self.logger.verbose("WebSocket stub server running")
        while self.ws_running is True:
            time.sleep(1)
        self.logger.debug("WebSocket stub server exiting")
        return

    def prepare_for_shutdown(self):
        # nothing special to do here, so just close because we need to stop consuming ASAP
        self.close()

    def close(self):
        self.logger.debug("wshandler.close() called")
        self.ws_running = False
        if self.ws_handle:
            self.ws_handle.close()

    def send_ws_message(self, msg):
        """
        Send a message to the WebSocket network.
        The intent is that if we fail to send (and return a 'False'), then it is up to the caller to figure out
        how to re-send the message if desired.
        :param msg: Message to send.
        :return: True if the operation was successful, False if otherwise.
        """
        # self.logger.debug(f"WebSocket: send_message to WebSocket (running={self.ws_running}) msg={msg}")
        if self.ws_connected is True and self.ws_running is True:
            try:
                self.ws_handle.send(msg)
            except Exception as e:
                self.logger.warn(f"Exception trying to send WS: {e}")
                return False

            if self.metrics and self.metrics.metrics_enabled:
                self.metrics.count_to_ws += 1
            return True
        else:
            if self.ws_running is True:
                # if ws_running is true, then we're just waiting for a connection so delay a moment
                self.logger.debug("WebSocket not yet connected; waiting a moment")
                time.sleep(1)
            else:
                self.logger.verbose("Server not running; can't send message!")
            return False

    def send_ws_message_stub(self, msg):
        """
        Stub method for sending a message to the WebSocket network, but instead it just briefly waits
        and then sends the message back to the on_message callback. Does not touch WebSocket API.
        :param msg: Message to send.
        :return: True if the operation was successful, False if otherwise.
        """
        # self.logger.debug(f"WebSocket: send_message_STUB to WebSocket (running={self.ws_running})")
        if self.ws_connected is True and self.ws_running is True:
            if STUB_SEND_DELAY > 0:
                time.sleep(STUB_SEND_DELAY)

            if self.metrics and self.metrics.metrics_enabled:
                self.metrics.count_to_ws += 1
                self.metrics.count_from_ws += 1
            self.on_message_handler(msg)
            return True
        else:
            if self.ws_running is True:
                # if ws_running is true, then we're just waiting for a connection so delay a moment
                self.logger.debug("WebSocket not yet connected; waiting a moment")
                time.sleep(1)
            else:
                self.logger.verbose("Server not running; can't send message!")
            return False


if __name__ == "__main__":
    print(f"{__file__} is not executable, it is just a library of functions for mq2wsbridge.py")
