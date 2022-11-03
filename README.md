# MessageBusBridge - A MQ to WebSockets message bridge

MessageBusBridge (MBB) is a relatively simple service that transfers messages between two different message buses. It was built for the purpose of providing users of WebSocket API services to have a quick and easy way to provide connectivity to their existing MQ bus systems without having to re-code to a WebSocket API. Effectively, it will listen to any message coming from the MQ bus and send it over to the WebSocket API, and vice-versa. While the service in this incarnation implements MQ to WebSockets, the code is modular so that the respective bus handling code can be swapped out for another bus, such as JMS or Kafka.



## Installing

MessageBusBridge (MBB) is written in Python 3 and is intended to run on an AWS EC2 instance running Amazon Linux. As the name implies, it is bridge between message busses so those along with some other optional services are covered below.

**Requirements**

* EC2 instance running Linux to host service.
* Python 3.9 or later with a few PIP modules (listed below)
* MQ server (Rabbit MQ was used during development).
* WebSockets message bus.
* AWS CloudWatch (optional log storage).
* AWS Systems Manager (optional configuration storage).


The MBB service consists of several .py files that must reside in the same directory. The service requires three Python modules to be installed: boto3, amqpstorm, and websocket-client. These can be installed by executing the following command:

`python3 -m pip install boto3 amqpstorm websocket-client`

## Configuration

By default the service operates in a standalone mode leveraging an .ini file for configuration and outputs log messages to STDOUT. Optionally, however, you can choose to AWS Systems Manager for configuration, and AWS CloudWatch can be used for logging.

### INI File

For standalone operation or deployments where the security of the INI file's contents do not pose any risk, the configuration of the system can be driven by an INI file.

The INI file is divided into three sections: MQ, WebSockets, and CloudWatch.  The file contains comments as to the individual settings.

### AWS Systems Manager

For container deployments or those where the details contained within an INI file cannot be left as plain text, AWS Systems Manager provides a secure mechanism for storing and retrieving configuration information. In the Systems Manager console, there is a section for 'Application Management' under which 'Parameter Store' can be found. MBB can store a number of elements in the parameter store under the `/mq2wsbridge` prefix, with group and element names same as found in the INI file.

While the AWS Systems Manager is accessible through the AWS console, the easiest way to get up-and-running is to first populate and test the values you need for the service in the INI file. Make sure these work, and then invoke the bridge program with the `--migrate-config` option which will read all the values from the INI file and store them in the AWS Systems Manager. Note that doing this migration option WILL overwrite existing configuration values that may be in place for MBB.

For the Parameter Store, the "Standard Tier" is sufficient as is using the default KMS key for your AWS account.

### Configuration Settings

The following is a list of the configuration settings and their purpose.

#### WebSocket API configuration (section ws_api)

##### client\_id / client\_secret

Settings `client_id` and `client_secret` are for websockets authentication. These values are provided by the websockets API service provider.

##### api\_host / api\_uri

`api_host` is the base of the websockets API URL, and api_uri is the second part of the URL, e.g. `${api_host}${api_uri}`. So a host of `wss://api.somewhere.com` plus a uri of `/path/to/ws` will expand into `wss://api.somewhere.com/path/to/ws`.

##### ws\_ping\_interval

Defines the number of seconds between sending automatic PINGs. If not defined, will default to 10 seconds.

##### ws\_max\_connect\_attempts

Defines the max number of attempts the wshandler will attempt to (re)connect before throwing an error and giving up. If not defined, will default to 10 attempts.

Note that since some WebSocket APIs might regularly disconnect clients, so this setting is tied to `ws_attempt_window_secs`.


##### ws\_attempt\_window\_secs

Defines the number of seconds that must pass before a websocket reconnect is considered "routine" and will not contribute to the `ws_max_connect_attempts` count. If not defined, it will default to 60 seconds.

In other words, the combination of `ws_max_connect_attempts` and `ws_attempt_windows_secs` is to detect a failure with the WebSocket API if the failures happen within a short period of time. The smaller the window, the quicker MBB will give up and fatally exit the service. As such, if you set the window secs and connect attempts to `0`, MBB will fail out due to any reconnect attempt.


#### MQ Broker configuration (section aws_mq)

##### mq\_broker\_id

This is the connection ID for the Amazon MQ service. The ID is the last part of the ARN of the MQ service (e.g. b-111111111-2222-3333-4444-55555555).

##### mq\_user\_id / mq\_password

Username and password to connect to the Amazon MQ service. These credentials can be obtained from the administrator of the MQ service, or through the administrative console of the MQ server itself (e.g. Rabbit MQ's Admin tab).

##### mq\_region / mq\_port

AWS region for the Amazon MQ service and the TCP/IP port that it is exposed on. Default port is 5671.


##### mq\_qname\_to\_ws / mq\_qname\_from\_ws

These are the names of the MQ queues for messages sent TO the websockets bus and received FROM. Actual names are defined on the MQ server, but the service will create them if they don't already exist.

##### mq\_ttl\_from\_ws

This is the number of milliseconds that a message received from the  websockets bus and bridged to MQ will live before automatically expired by the MQ broker. in other words, messages that end up on `mq_qname_from_ws` will get this time-to-live (TTL) applied to them.

Setting a high TTL for these messages means that consumers on the MQ bus have longer to retrieve the messages before they are expired. However, this may result in larger MQ server persistent store requirements.

##### mq\_consumer\_tag

MQ consumer tag name that will be used with the MQ server. This is for labeling and identification purposes of the MBB on the MQ server. Can be set to whatever you want, but cannot be blank.


#### AWS CloudWatch configuration (section aws_cloudwatch)

##### cw\_retention\_days

The number of days log messages published to CloudWatch will be retained for before they are removed. May be overridden on the AWS console. If not specified, will default to 30 days.

##### cw\_region

CloudWatch region to publish log messages to. Must be specified or MBB will not start when either CloudWatch logs or metrics are enabled.

##### cw\_log\_stream

CloudWatch log stream name to publish to. Must be specified or MBB will not start when CloudWatch logs are enabled.

##### cw\_log\_group
CloudWatch log group to publish to. Must be specified or MBB will not start when CloudWatch logs are enabled.

This is simply a /slash/delimited/path to a log group where logs will be published. Can be whatever you want. Can be named the same as `cw_metrics_namespace`; they will not clash since they're separate sections in CloudWatch.


##### cw\_metrics\_namespace

CloudWatch metrics namespace to publish to. Must be specified or MBB will not start when metrics are enabled.

This is simply a /slash/delimited/path to a namespace where metrics will be published. Can be whatever you want. Can be named the same as `cw_log_group`; they will not clash since they're separate sections in CloudWatch.


##### cw\_metrics\_resolution

Number of seconds MBB will wait between publishing metrics to CloudWatch. If not specified, will default to 10 seconds.



## Running MessageBusBridge

MBB is run from the command line with a few arguments. The output of the `--help` screen is below:

<pre>
usage: mq2wsbridge.py [-h] [--runsecs RUN_SECS] [-v] [-d] [-c CONFIG] [-r SSM_REGION] [-s] [-M] [-X] [-l]
                      [-m]

MQ to WebSocket message bridge service.

options:
  -h, --help            show this help message and exit
  --runsecs RUN_SECS    number of seconds to run before shutting down, 0=forever (default: 60)
  -v, --verbose         display lots of information while running (e.g. what modules are executing)
  -d, --debug           display debug level of information while running (e.g. messages being published)
  -c CONFIG, --config CONFIG
                        config file to use (default: mq2wsbridge.ini)
  -r SSM_REGION, --ssm-region SSM_REGION
                        systems manager config region to use instead of native one; specify as aws region
                        (e.g. 'us-east-1')
  -s, --ssm             use systems manager for config; overrides -c config
  -M, --migrate-config  migrate INI configuration to systems manager. requires -s. will overwrite
                        existing ssm!
  -X, --websocket-stub  stub out the WebSocket handler for testing, just echo messages back
  -l, --cloudwatch-logs
                        use CloudWatch logging instead of STDOUT
  -m, --cloudwatch-metrics
                        use CloudWatch for recording metrics
</pre>


#### --cloudwatch-logs (-l)

By default, log output goes to STDOUT. By using this argument, STDOUT is disabled and instead logs are sent to CloudWatch. The region for this is defined in the configuration INI/SSM under `aws_cloudwatch/cw_region`. See configuration file for further settings which influence CloudWatch logs, paths, and retention.

#### --cloudwatch-metrics (-m)

By default, no metrics are captured by the system. By using this argument, metrics are captured and sent to CloudWatch. The region for this is defined in the configuration INI/SSM under `aws_cloudwatch/cw_region`. See configuration file for further settings which influence CloudWatch metrics, namespace, and frequency of updates.

#### --config *\<ini_file\>* (-c)

By default, system will attempt to use an INI file in the current directory called `mq2wsbridge.ini`. Specifying this argument will override that default and instead use the configuration file specified.

If AWS Systems Manager Parameter Store is used instead for configuration, this setting is ignored.

#### --migrate-config (-M)

Performs a one-off migration of the configuration settings in the INI file to the AWS Systems Manager Parameter Store. This is a one-way operation and overwrites existing values.

Requires SSM region to be specified. All values are stored as Secure Strings.

#### --runsecs *\<number\>*

Specifying this argument will cause MBB to self-shutdown after the specified number of seconds. By default, MBB assumes a runsecs value of `0` which is equivalent to "run forever".

#### --ssm (-s)

Use AWS Systems Manager Parameter Store instead of the INI file for configuration. By default, the INI file is used.

#### --ssm-region *\<region_name\>* (-r)

If AWS Systems Manager is used for configuration, the current region where MBB is running is assumed. Specifying this argument will override the assumed region with the one specified.

#### --websocket-stub (-X)

Uses a stub instead of connecting to the WebSocket API. In other words, any messages received by the bridge on the MQ "to WebSocket" queue will be echoed back to the "from WebSocket" queue instead of being bridged over to the WebSocket API. Used for testing of the MQ setup.

#### --debug (-d)

Enable DEBUG level output in log messages. This level of message is suppressed by default. Note that specifying this argument does NOT imply VERBOSE level messages as well; those need to be separately enabled.

#### --verbose (-v)

Enable VERBOSE / INFO level output in log messages. This level of message is suppressed by default. Note that specifying this argument does NOT imply DEBUG level messages as well; those need to be separately enabled.


## Testing Bridge Service

The MBB package is made up of several Python files. The modules are named with a suffix of 'handler' (e.g. mqhandler, confighandler) and do not operate on their own. The two other python files that are not 'handlers' are meant to be launched, one naturally being the `mq2wsbridge.py` main service, but there is also `bridgetester.py`.

The purpose of the bridge tester is that it provides a means of checking setup, connectivity, and performance. It simply sends out timestamped sample messages to the bridge and tracks that they come back and how long it took. The number, speed, and visual representation can be selected through command line arguments. The output of the `--help` argument is below:

<pre>
usage: bridgetester.py [-h] [-v] [-n NUM_MSGS] [-d MSG_DELAY] [-x] [-e END_DELAY] [-g] [-r REPORT_FILE] [-f] [-c CONFIG_FILE]

MQ to WebSocket Bridge Tester.

options:
  -h, --help            show this help message and exit
  -v, --visual          displays a curses-based table of when messages are sent/received and elapsed time
  -n NUM_MSGS, --number NUM_MSGS
                        number of test messages to be sent before quitting (default: 8)
  -d MSG_DELAY, --delay MSG_DELAY
                        number of seconds to delay between messages (default: 1)
  -x, --exclusive       exclusive mode, i.e. we will consume any message we receive regardless if it was for us (bad for
                        production environments, but good for clearing out stale test run messages)
  -e END_DELAY, --end-delay END_DELAY
                        number of seconds to delay at end if not all messages have been reconciled (default: 30)
  -g, --graph           displays a visual of what messages have been reconciled, but does not provide details
  -r REPORT_FILE, --report REPORT_FILE
                        generate file report which messages were non-reconciled at the end of the run
  -f, --focused         output is focused only on message we sent; do not bother reporting others we may reject (only applies
                        to non-visual/graph modes)
  -c CONFIG_FILE, --config CONFIG_FILE
                        config file to use (default: mq2wsbridge.ini)
</pre>



#### --config *\<CONFIG_FILE\>*

Specify a configuration INI file to use. Defaults to `mq2wsbridge.ini` if omitted.

#### --number *\<NUM_MSGS\>*

Number of test messages to send before reconciling gaps and quitting. Defaults to 8.

#### --delay *\<MSG_DELAY\>*

Number of seconds (floating point) to delay between sending messages. Can be 0 (no delay) or any positive value (e.g. 0.005, 3, etc.). Defaults to 1.

#### --end-delay *\<END_DELAY\>*

Number of seconds to delay at end if not all messages have been reconciled. Defaults to 30 seconds. Useful in visual and graph modes to avoid having the screen disappear if there are still gaps from non-reconciled messages.

#### --exclusive

Exclusive mode will consume any messages that it receives from the MQ server regardless of whether or not they're intended for the test program.

The idea is that the MQ server will send any message it has to any consumers that are subscribed to its queues. As such, one consumer may receive messages that another consumer is expecting. The test program in non-exclusive mode will reconcile any message it receives with an internal list of messages that it has sent out, and will reject and requeue any messages that it receives which are not on the list of message it is expecting, thus giving another consumer a chance to receive the message it is expecting.

The purpose of exclusive mode is that the bridge tester will always consume whatever is sent to it, regardless of whether or not the received message was intended for it. This should ONLY be used in scenarios where other consumers that may be subscribed to the MQ bus can lose messages. Useful in clearing out the MQ server's queue.

#### --focused

Testing output will only report on messages that are intended for this instance of the bridge tester and will not report messages that are rejected.

This option does not apply to non-visual/graph operation modes.

#### --visual

Displays test output in a visual curses-based table format of when messages are sent/received, and elapsed time. Cannot be used with `--graph`.

#### --graph

Displays test output as a visual of messages sent and what has been reconciled, but does not display details. Cannot be used with `--visual`.

#### --report *\<REPORT_FILE\>*

Generates a report file of which messages were unresolved at the end of the test run.



## AWS Services

MBB at a minimum is intended to run on an Amazon Linux EC2 instance. Beyond that, it uses Amazon MQ, CloudWatch, and AWS Systems Manager.

The software needs to run on an EC2 instance that has an IAM role suitable for working with these services.


## Getting started

The following instructions assume that the WebSocket and MQ services are already set up and accessible for your AWS account.

1. Create a filesystem directory on the EC2 instance you want to run MBB on.
2. Copy MBB Python files to the directory; there is no directory structure required, they all reside in the same place.
3. Edit the `mq2wsbridge.ini` file as per documentation. Note that you can create alternate INI files for testing purposes (see --config option).
4. Start up the service with debug and verbose logging enabled for the first run to smoke test that the permissions are correct and MBB can communicate with the various AWS services and APIs.
5. If no errors are thrown, run with the appropriate --runsecs option in an appropriate foreground/background mode.



## Considerations

##### Logging Levels

By default, MBB will report WARN and ERROR level messages. To enable INFO (verbose) and DEBUG, use the appropriate command line arguments. Note that they are not inclusive, i.e. debug does not imply verbose; they need to be individually specified.

As typical, DEBUG level logging may be excessive in normal operation and will cause a slight performance penalty due to the extra message output.

##### AWS Systems Manager vs INI Files

INI files are easy, but are inherently insecure since they are plain-text files. As such, it might be preferred to use AWS Systems Manager and its parameter store as this can be permissioned in such a way that can hide details from unauthorized entities and also support regulatory compliance.

The easiest way to set up the parameter store is to craft the INI file to what you want, and then use the --migrate-config feature of the MBB application. After that, any changes can be done from the AWS Systems Manager console, if desired.

##### WebSocket Stub

The purpose of the websocket stub is for testing that your service works, without actually sending any messages to the websocket server. In this mode, the websocket server will not be connected and any messages intended for the websocket server will instead simply be echoed back to the MQ service. In this mode, MQ messages are consumed as if they were "production", i.e. they're not requeued or otherwise treated as test messages.

##### Running Multiple MBB Instances

MBB will happily run in parallel with multiple, similarly-configured instances to get High Availability and increased performance. The MBB instances do not communicate with each other in any way; rather they simply operate as peer consumers to the MQ service. The sequence, prioritization, and behavior of multiple instances is a function of the MQ service and how it handles multiple consumers and cannot be influenced by MBB.

On the WebSocket service side, it is possible that your WS propvider doesn't allow more than one connection. If this is the case, then launching a second instance of MBB will fail out quickly as a connection will not be established. Note that MBB will not start consuming/producing messages if either the WebSocket or MQ connections cannot be established.

<hr/>

## Support

If you find any issues with the code, please raise an issue ticket in the tracker.

## Roadmap

The service was written to be modular, so ideas for future improvements include supporting other messaging buses. To be revisted, but raising a ticket in the issue tracker would be a good way to show interest!

## Contributing

Contributions are preferred to be done via pull requests. Please message me if you are interested in contributing.

From the development side of things, this was developed using PyCharm and the default Python code formatter is used. On a personal level, 4 spaces are preferred over tab characters for indentations!


## License

This project is licensed under the Apache 2.0 license.
