#!/usr/bin/python
# -*- coding: UTF-8 -*-

from __future__ import print_function
import time
import picamera
import json
import sys
from tentacle_pi.AM2315 import AM2315
from switchdoc import TCA9545
from switchdoc import Test_TCA9545

class SensorException(Exception) :
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

def main():
    if len(sys.argv) < 2:
        print("Usage: sense_and_record.py <PATH_TO_JSON_CONFIG>")
        sys.exit(127)
    print("Starting Sense and Record v2.0")
    with open(sys.argv[1]) as json_config_file:
        config = json.load(json_config_file)
        if config['output_dir']:
            with picamera.PiCamera() as camera:
                # Prep the mux.
                # Note: This allows us to talk to up to 4 devices with the same address.
                tca9545 = TCA9545.SDL_Pi_TCA9545(addr=TCA9545.TCA9545_ADDRESS, bus_enable = TCA9545.TCA9545_CONFIG_BUS0)
                am2315 = AM2315(0x5c, "/dev/i2c-1")
                camera.hflip = True
                camera.vflip = True

                last_image_taken = 0

                while True:
                    timestamp = time.mktime(time.localtime())
                    print("*" * 80)
                    print("Reading Sensors...")
                    try:
                        print("Internal Sensor:")
                        sense(am2315, tca9545, TCA9545.TCA9545_CONFIG_BUS0)
                    except SensorException as e :
                        print("CRITICAL: Internal Weather Sensor Failed to Read.")
                    except Exception as e:
                        print("CRITICAL: I2C Bus Read Error")

                    try:
                        print("External Sensor:")
                        sense(am2315, tca9545, TCA9545.TCA9545_CONFIG_BUS1)
                    except SensorException as e:
                        print("CRITICAL: External Weather Sensor Failed to Read.")
                    except Exception as e:
                        print("CRITICAL: I2C Bus Read Error")

                    time_since_last_image_taken = time.mktime(time.localtime()) - last_image_taken
                    if time_since_last_image_taken > (60 * 30):
                        try:
                            print("Initializing Camera...")
                            # Prep the camera for use
                            camera.resolution = (3280, 2464)
                            camera.framerate = 30
                            time.sleep(2)  # initialize time
                            camera.shutter_speed = camera.exposure_speed
                            camera.exposure_mode = 'off'
                            g = camera.awb_gains
                            camera.awb_mode = 'off'
                            camera.awb_gains = g
                            print("Snapping Image...", end="")
                            camera.capture_sequence(['%s/image_%02d.jpg' % (config['output_dir'], timestamp)])
                            print("    [OK]")
                            last_image_taken = time.mktime(time.localtime())
                        except:
                            print("    [FAILED]")
                            print("CRITICAL: Camera Read Error")

                    else:
                        print("Next camera image will be taken in %ldm..." % int(((60 * 30) - time_since_last_image_taken) / 60))
                    delta = (time.mktime(time.localtime()) - timestamp)
                    sleep_len = (60 - delta)
                    print("Sleeping for %lds..." % sleep_len)
                    print()
                    time.sleep(sleep_len)


        else:
            print("Missing 'output_dir' in config file!")
            sys.exit(125)



def sense(sensor, mux, bus):
    # Point the mux to the first bus
    mux.write_control_register(bus)
    control_register = mux.read_control_register()

    if control_register & 0x0f == bus:
        # Grab sensor info from that bus
        temp_c, humidity, crc_check = sensor.sense()
        if  crc_check == 1:
            temp_f = temp_c * (9.0 / 5.0) + 32.0
            print("    Temperature: %0.1f°F" % temp_f)
            print("    Humidity:    %0.1f%%" % humidity)
            print()
        else:
            raise SensorException(bus)

main()

# test = Test_TCA9545.TestTCA9545()
# test.test()