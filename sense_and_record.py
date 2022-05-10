#!/usr/bin/python
# -*- coding: UTF-8 -*-


import time
from datetime import datetime
import sqlite3
import os
import subprocess
import re
import time
from picamera2.picamera2 import Picamera2
import json
import sys
import SHT30
import TCA9545

class SenseAndRecord:

    SECONDS_IN_MINUTE   = 60.0
    MINUTES_IN_HOUR     = 60.0
    SECONDS_IN_HOUR     = SECONDS_IN_MINUTE * MINUTES_IN_HOUR
    HOURS_IN_DAY        = 24.0
    MINUTES_IN_DAY      = MINUTES_IN_HOUR * HOURS_IN_DAY
    SECONDS_IN_DAY      = SECONDS_IN_HOUR * HOURS_IN_DAY

    CAMERA_INITIALIZE_TIME = 2.0

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
        self._tca9545 = TCA9545.TCA9545(addr=TCA9545.TCA9545_ADDRESS, bus_enable=TCA9545.TCA9545_CONFIG_BUS0)
        self._sht30 = SHT30.SHT30(powerpin=6)
        #self._camera = picamera.PiCamera()
        try:
            os.makedirs(self._output_dir)
        except:
            pass
        self._initialize_database()

    def _initialize_database(self):
        self._db = sqlite3.connect("%s/greenhouse_data.sqlite" % self._output_dir)
        self._db.execute('PRAGMA encoding = \"UTF-8\";')

        self._db.execute('CREATE TABLE IF NOT EXISTS "data_points" ("id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "timestamp" integer, "synchronized" integer DEFAULT 0);')
        self._db.execute('CREATE INDEX IF NOT EXISTS "index_data_points_on_timestamp" ON "data_points" ("timestamp");')

        self._db.execute('CREATE TABLE IF NOT EXISTS "sensor_data" ("id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "sensor_id" integer, "temperature" float, "humidity" float, "data_point_id" integer);')
        self._db.execute('CREATE INDEX IF NOT EXISTS "index_sensor_data_on_data_point_id" ON "sensor_data" ("data_point_id");')

        self._db.execute('CREATE TABLE IF NOT EXISTS "system_data" ("id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "soc_temperature" float, wlan0_link_quality float, wlan0_signal_level integer, storage_total_size integer,storage_used integer, storage_avail integer, "data_point_id" integer);')
        self._db.execute('CREATE INDEX IF NOT EXISTS "index_system_data_on_data_point_id" ON "system_data" ("data_point_id");')

        self._db.execute('CREATE TABLE IF NOT EXISTS "image_data" ("id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "filename" text, "data_point_id" integer);')
        self._db.execute('CREATE INDEX IF NOT EXISTS "index_image_data_on_data_point_id" ON "image_data" ("data_point_id");')
        # Rails Migration data. In the event this is run before rails migrations are run we need to let rails know we
        # are already setup for it
        self._db.execute('CREATE TABLE IF NOT EXISTS "schema_migrations" ("version" varchar NOT NULL);')
        self._db.execute('CREATE UNIQUE INDEX IF NOT EXISTS "unique_schema_migrations" ON "schema_migrations" ("version");')
        try:
            self._db.execute('INSERT INTO "schema_migrations" ("version") VALUES (20160529015914)')
        except sqlite3.IntegrityError:
            pass
        try:
            self._db.execute('INSERT INTO "schema_migrations" ("version") VALUES (20160529020002)')
        except sqlite3.IntegrityError:
            pass
        try:
            self._db.execute('INSERT INTO "schema_migrations" ("version") VALUES (20160529020220)')
        except sqlite3.IntegrityError:
            pass
        try:
            self._db.execute('INSERT INTO "schema_migrations" ("version") VALUES (20160529191804)')
        except sqlite3.IntegrityError:
            pass
        
        self._db.commit()
        self._db.close()

    def _initialize_camera(self):
        print("Initializing Camera...")
        # Prep the camera for use
        self_camera = Picamera2()

        config = self._camera.still_configuration(raw={"size": self_camera.sensor_resolution})
        picam2.configure(config)
        self_camera.start()

        print('Waiting %1.0fs for the camera to settle...' % SenseAndRecord.CAMERA_INITIALIZE_TIME)
        time.sleep(SenseAndRecord.CAMERA_INITIALIZE_TIME)
        
    def validate_mount(self):
        valid = False
        try:
            with open("%s/volume_info.json" % self._config['external_share']) as file_contents:
                share_info = json.load(file_contents)
                valid = share_info['storage_name'] == self._config['share_validation_string']
        except:
            valid = False
        return valid

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
                   cursor.execute("INSERT INTO data_points(timestamp) VALUES (?)", (timestamp,));
                   data_point_id = cursor.lastrowid
                   # TODO: Try to align the image time with half hour bounaries
                   self._sense_weather(cursor, data_point_id)
                   
                   self._get_system_data(cursor,data_point_id)

                   if time_since_last_image_taken >= (SenseAndRecord.SECONDS_IN_MINUTE * self._minutes_between_image_acquisitions):
                       # TODO: Try to align the image time with half hour bounaries
                       self._acquire_image(cursor, data_point_id, timestamp)
                   else:
                       print("Next camera image will be taken in %ldm...\n" % int(((SenseAndRecord.SECONDS_IN_MINUTE * self._minutes_between_image_acquisitions) - time_since_last_image_taken) / SenseAndRecord.SECONDS_IN_MINUTE))
                       
                   print()

               self._db.commit()
               self._db.close()

               delta = time.mktime(time.localtime()) - timestamp
               sleep_len = (5.0 - delta) if (delta <= 5.0) else 0.0
               time.sleep(sleep_len)
        else:
            print("Missing 'output_dir' in config file!")
            sys.exit(125)

    def _sense_weather(self, cursor, data_point_id):
        print(("*" * 80))
        print(("%d - %s" % (data_point_id, time.strftime("%m/%d/%Y %H:%M:%S"))))
        print("Reading Sensors...\n")
        try:
            print("Internal Sensor:")
            self._sense_weather_on_bus(cursor, data_point_id, TCA9545.TCA9545_CONFIG_BUS0)
        except SenseAndRecord.SensorException as e:
            print("CRITICAL: Internal Weather Sensor Failed to Read.")
        except Exception as e:
            print(("CRITICAL: I2C Bus Read Error - ", e))

        try:
            print("External Sensor:")
            self._sense_weather_on_bus(cursor, data_point_id, TCA9545.TCA9545_CONFIG_BUS1)
        except SenseAndRecord.SensorException as e:
            print("CRITICAL: External Weather Sensor Failed to Read.")
        except Exception as e:
            print(("CRITICAL: I2C Bus Read Error - ", e))

        self._last_weather_sensed = time.mktime(time.localtime())

    def _sense_weather_on_bus(self, cursor, data_point_id, bus):
        # Point the mux to the first bus
        self._tca9545.write_control_register(bus)
        control_register = self._tca9545.read_control_register()

        if control_register & 0x0f == bus:
            # Grab sensor info from that bus
            humidity, temp_c, crc_ch, crc_ct = self._sht30.read_humidity_temperature_crc()
            if True:
                print(("    Temperature:        %0.1f°F" % self._celsius_to_fahrenheit(temp_c)))
                print(("    Humidity:           %0.1f%%" % humidity))
                print(("." * 80))
                cursor.execute("INSERT INTO sensor_data(sensor_id, temperature, humidity, data_point_id) VALUES (?, ?, ?, ?)", (bus, temp_c, humidity, data_point_id));
            else:
                print(("CRC: %d %d" % crc_ch, crc_ct)) 
                raise SenseAndRecord.SensorException(bus)
        return (temp_c, humidity)

    def _get_system_data(self, cursor, data_point_id):
        print("System Info:")
        try:
            # Get SOC Temp
            system_temp_file = open("/sys/class/thermal/thermal_zone0/temp", "r")
            temperature_data_string = system_temp_file.readline()
            soc_temperature = float(temperature_data_string)/1000.0
            # Get Wifi Info
            iwconfig_output = subprocess.check_output(["/usr/sbin/iwconfig", "wlan0"]).decode('utf8')
            link_quality_match = re.match(r'.*Link Quality=([0-9]{,3}/[0-9]{,3})', iwconfig_output, re.MULTILINE | re.DOTALL)
            link_quality_string = link_quality_match.group(1) # Gives value as x/y
            num, den = link_quality_string.split("/")
            link_quality = float(num)/float(den)
            link_signal_match = re.match(r'.*Signal level=(-?[0-9]{,3})\s*dBm', iwconfig_output, re.MULTILINE | re.DOTALL)
            link_signal_string = link_signal_match.group(1)
            link_signal = 30#int(link_signal_string)
            # Disk Stats
            df_output = subprocess.check_output(["df", "/"]).decode('utf8')
            dev, size, used, avail, percent, mountpoint = df_output.split("\n")[1].split()

            cursor.execute("INSERT INTO system_data(soc_temperature, wlan0_link_quality, wlan0_signal_level, storage_total_size, storage_used, storage_avail, data_point_id) VALUES (?, ?, ?, ?, ?, ?, ?);", (soc_temperature, link_quality, link_signal, int(size), int(used), int(avail), data_point_id));
            print(("    SOC Temperature:    %0.1f°F" % self._celsius_to_fahrenheit(soc_temperature)))
            print(("    wlan0 Link Quality: %0.2f%%" % (100.0*link_quality)))
            print(("    wlan0 Signal Level: %d dBm" % link_signal))
            print(("    Storage Used:       %0.1f%%" % (100.0*(float(used)/float(size)))))
            print(("." * 80))
        except IOError as e:
            print(("WARNING: Unable to open system temperature file.", e))
        except Exception as e:
            print(("CRITICAL: Unable to insert system temperature data. ", e))

    def _acquire_image(self, cursor, data_point_id, timestamp):
       print("Camera:")
       try:
           print("    Snapping Image...", end="")
           output_dir = self._output_dir
           if self.validate_mount():
               output_dir = self._config['external_share']
           else:
               print("[WARNING]: External share is not mounted properly.  Saving images locally.")

           date_subfolders = datetime.fromtimestamp(timestamp).strftime('%Y/%m/%d')
           friendly_timestamp = datetime.fromtimestamp(timestamp).strftime('%H_%M_%S')
           images_path = '%s/%s/%s' % (output_dir, self._config['image_subfolder'], date_subfolders)
           try:
               os.makedirs(images_path)
           except OSError as e:
               pass
           except Exception as e:
               print(e)
           filename = '%s/img_%02d_%s.jpg' % (images_path, timestamp, friendly_timestamp)
           self._camera.capture_file([filename])
           cursor.execute("INSERT INTO image_data(filename, data_point_id) VALUES (?, ?);", (filename, data_point_id));
           print("   [OK]\n")
           print("." * 80)
           self._last_image_taken = time.mktime(time.localtime())
       except Exception as e:
           print("   [FAILED]\n")
           print("CRITICAL: Camera Read Error - ", e)
           print("." * 80)

    def _celsius_to_fahrenheit(self, celsius):
        return (celsius * (9.0 / 5.0) + 32.0)




if len(sys.argv) < 2:
    print("Usage: sense_and_record.py <PATH_TO_JSON_CONFIG>")
    sys.exit(127)

snr = SenseAndRecord(sys.argv[1])
snr.sense_and_record()
