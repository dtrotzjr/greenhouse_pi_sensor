#!/usr/bin/python

import time
import picamera
import json
import sys
from tentacle_pi.AM2315 import AM2315
am = AM2315(0x5c, "/dev/i2c-1")

if len(sys.argv) < 2:
    print "Usage: sense_and_record.py <PATH_TO_JSON_CONFIG>"
    sys.exit(127)

with open(sys.argv[1]) as json_config_file:
    config = json.load(json_config_file)
    if config['output_dir']:

        with picamera.PiCamera() as camera:
            camera.resolution = (3280, 2464)
            camera.framerate = 30
            time.sleep(2)
            camera.shutter_speed = camera.exposure_speed
            camera.exposure_mode = 'off'
            g = camera.awb_gains
            camera.awb_mode = 'off'
            camera.awb_gains = g
            i = 0
            while True:
                i += 1
                temp_c, humidity, crc_check = am.sense()
                temp_f = temp_c * (9.0/5.0) + 32.0
                print "temperature: %0.1f F" % temp_f
                print "humidity: %0.1f" % humidity
                print "crc_check: %s" % crc_check
                print
                #camera.capture_sequence(['%s/image%02d.jpg' % (config['output_dir'], i)])
                time.sleep(2)

    else:
        print("Missing 'output_dir' in config file!")
        sys.exit(125)


