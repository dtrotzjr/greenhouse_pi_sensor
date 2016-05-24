#!/usr/bin/python
# -*- coding: UTF-8 -*-

from __future__ import print_function
import time
import sqlite3
import os
import datetime
import picamera
import json
import sys
from tentacle_pi.AM2315 import AM2315
from switchdoc import TCA9545
from switchdoc import Test_TCA9545

class SenseAndRecord:

    SECONDS_IN_MINUTE   = 60.0
    MINUTES_IN_HOUR     = 60.0
    SECONDS_IN_HOUR     = SECONDS_IN_MINUTE * MINUTES_IN_HOUR
    HOURS_IN_DAY        = 24.0
    MINUTES_IN_DAY      = MINUTES_IN_HOUR * HOURS_IN_DAY
    SECONDS_IN_DAY      = SECONDS_IN_HOUR * HOURS_IN_DAY

    class SensorException(Exception):
        def __init__(self, value):
            self.value = value

        def __str__(self):
            return repr(self.value)

    def __init__(self, config_file_name):
        with open(config_file_name) as json_config_file:
            self._config = json.load(json_config_file)
            self._output_dir = self._config['output_dir']
            self._minutes_between_sensor_readings = float(self._config["minutes_between_sensor_readings"])
            self._minutes_between_image_acquisitions = float(self._config["minutes_between_image_acquisitions"])

        self._last_image_taken = 0
        self._last_weather_sensed = 0
        # Prep the mux.
        # Note: This allows us to talk to up to 4 devices with the same address.
        self._tca9545 = TCA9545.SDL_Pi_TCA9545(addr=TCA9545.TCA9545_ADDRESS, bus_enable=TCA9545.TCA9545_CONFIG_BUS0)
        self._am2315 = AM2315(0x5c, "/dev/i2c-1")
        self._camera = picamera.PiCamera()
        try:
            os.makedirs(self._output_dir)
        except:
            pass
        try:
            os.makedirs("%s/imgs" % self._output_dir)
        except:
            pass
        self._initialize_database()

    def _initialize_database(self):
        self._db = sqlite3.connect("%s/greenhouse_data.sqlite" % self._output_dir)
        self._db.execute("PRAGMA encoding = \"UTF-8\";")

        self._db.execute("CREATE TABLE IF NOT EXISTS data_point (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER, synchronized INTEGER DEFAULT 0)")
        self._db.execute("CREATE INDEX IF NOT EXISTS data_point_timestamp_idx ON data_point(timestamp)")

        self._db.execute("CREATE TABLE IF NOT EXISTS data_point_sensor_data (id INTEGER PRIMARY KEY, data_point_id INTEGER REFERENCES data_point(id) ON DELETE CASCADE, sensor_data_id INTEGER REFERENCES sensor_data(id) ON DELETE NO ACTION)")
        self._db.execute("CREATE INDEX IF NOT EXISTS data_point_sensor_data_lhs_idx ON data_point_sensor_data(data_point_id)")
        self._db.execute("CREATE INDEX IF NOT EXISTS data_point_sensor_data_rhs_idx ON data_point_sensor_data(sensor_data_id)")

        self._db.execute("CREATE TABLE IF NOT EXISTS sensor_data (id INTEGER PRIMARY KEY, sensor_id INTEGER, temperature_c REAL, humidity REAL)")

        self._db.execute("CREATE TABLE IF NOT EXISTS image_data (id INTEGER PRIMARY KEY, filename TEXT, data_point_id INTEGER REFERENCES data_point(id) ON DELETE CASCADE)")
        self._db.close()


    def _initialize_camera(self):
        print("Initializing Camera...")
        # Prep the camera for use
        self._camera.resolution = (3280, 2464)
        self._camera.framerate = 30
        # The camera requires some time to initialize
        time.sleep(2)
        self._camera.shutter_speed = self._camera.exposure_speed
        self._camera.exposure_mode = 'off'
        g = self._camera.awb_gains
        self._camera.awb_mode = 'off'
        self._camera.awb_gains = g
        self._camera.hflip = True
        self._camera.vflip = True

    def sense_and_record(self):
        print("Starting Sense and Record v2.0")

        if self._config['output_dir']:
            self._initialize_camera()

            while True:
                self._db = sqlite3.connect("%s/greenhouse_data.sqlite" % self._output_dir)

                can_print_next_image_message = False
                timestamp = time.mktime(time.localtime())

                time_since_last_weather_sensed = time.mktime(time.localtime()) - self._last_weather_sensed
                time_since_last_image_taken = time.mktime(time.localtime()) - self._last_image_taken

                if time_since_last_weather_sensed >= (SenseAndRecord.SECONDS_IN_MINUTE * self._minutes_between_sensor_readings) or time_since_last_image_taken >= (SenseAndRecord.SECONDS_IN_MINUTE * self._minutes_between_image_acquisitions):
                    cursor = self._db.cursor()
                    cursor.execute("INSERT INTO data_point(timestamp) VALUES (?)", (timestamp,));

                    # TODO: Try to align the image time with half hour bounaries
                    self._sense_weather(cursor, cursor.lastrowid)

                    if time_since_last_image_taken >= (SenseAndRecord.SECONDS_IN_MINUTE * self._minutes_between_image_acquisitions):
                        # TODO: Try to align the image time with half hour bounaries
                        self._acquire_image(cursor, cursor.lastrowid, timestamp)
                    else:
                        print("Next camera image will be taken in %ldm...\n" % int(((SenseAndRecord.SECONDS_IN_MINUTE * self._minutes_between_image_acquisitions) - time_since_last_image_taken) / SenseAndRecord.SECONDS_IN_MINUTE))

                self._db.commit()
                self._db.close()

                delta = time.mktime(time.localtime()) - timestamp
                sleep_len = (5.0 - delta) if (delta <= 5.0) else 0.0
                time.sleep(sleep_len)

        else:
            print("Missing 'output_dir' in config file!")
            sys.exit(125)

    def _sense_weather(self, cursor, data_point_id):
        print("*" * 80)
        print("%d - %s" % (data_point_id, time.strftime("%m/%d/%Y %H:%M:%S")))
        print("Reading Sensors...")
        try:
            print("Internal Sensor:")
            self._sense_weather_on_bus(cursor, data_point_id, TCA9545.TCA9545_CONFIG_BUS0)
        except SenseAndRecord.SensorException as e:
            print("CRITICAL: Internal Weather Sensor Failed to Read.")
        except Exception as e:
            print("CRITICAL: I2C Bus Read Error - ", e)

        try:
            print("External Sensor:")
            self._sense_weather_on_bus(cursor, data_point_id, TCA9545.TCA9545_CONFIG_BUS1)
        except SenseAndRecord.SensorException as e:
            print("CRITICAL: External Weather Sensor Failed to Read.")
        except Exception as e:
            print("CRITICAL: I2C Bus Read Error - ", e)
        print()

        self._last_weather_sensed = time.mktime(time.localtime())

    def _sense_weather_on_bus(self, cursor, data_point_id, bus):
        # Point the mux to the first bus
        self._tca9545.write_control_register(bus)
        control_register = self._tca9545.read_control_register()

        if control_register & 0x0f == bus:
            # Grab sensor info from that bus
            temp_c, humidity, crc_check = self._am2315.sense()
            if crc_check == 1:
                temp_f = temp_c * (9.0 / 5.0) + 32.0
                print("    Temperature: %0.1f°F" % temp_f)
                print("    Humidity:    %0.1f%%" % humidity)
                print()
                cursor.execute("INSERT INTO sensor_data(sensor_id, temperature_c, humidity) VALUES (?, ?, ?)", (bus, temp_c, humidity));
                cursor.execute("INSERT INTO data_point_sensor_data(data_point_id, sensor_data_id) VALUES (?, ?)", (data_point_id, cursor.lastrowid));
            else:
                raise SenseAndRecord.SensorException(bus)
        return (temp_c, humidity)

    def _acquire_image(self, cursor, data_point_id, timestamp):
        try:
            print("Snapping Image...", end="")
            prefix = time.strftime("%Y_%m_%d_%H_%M_%S")
            filename = '%s/imgs/img_%02d_%s.jpg' % (self._output_dir, timestamp, prefix)
            self._camera.capture_sequence([filename])
            cursor.execute("INSERT INTO image_data(filename, data_point_id) VALUES (?, ?);", (filename, data_point_id));
            print("    [OK]\n")
            self._last_image_taken = time.mktime(time.localtime())
        except Exception as e:
            print("    [FAILED]\n")
            print("CRITICAL: Camera Read Error - ", e)




if len(sys.argv) < 2:
    print("Usage: sense_and_record.py <PATH_TO_JSON_CONFIG>")
    sys.exit(127)

snr = SenseAndRecord(sys.argv[1])
snr.sense_and_record()
