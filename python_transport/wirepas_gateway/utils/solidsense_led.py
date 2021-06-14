# -------------------------------------------------------------------------------
# Name:        solidsense_led
# Purpose:
#
# Author:      Laurent Carré / Nicolas Albarel
#
# Created:     31/12/2019
# Copyright:   (c) Laurent Carré Sterwen Technologies 2019
# Licence:     <your licence>
# -------------------------------------------------------------------------------


import time
import threading
import os


class SolidSenseLed:

    led_path = "/sys/devices/soc0/leds/leds"
    leds = [None, None, None, None, None]
    init = False

    @staticmethod
    def detect_leds():
        for entry in os.scandir(SolidSenseLed.led_path):
            # print (entry.name)
            lent = len(entry.name)
            led_num = int(entry.name[lent - 1 : lent])
            color = entry.name[: lent - 1]
            led = SolidSenseLed.leds[led_num]
            if led is not None:
                led.add_color(color)
            else:
                led = LedWrapper(led_num)
                led.add_color(color)
                SolidSenseLed.leds[led_num] = led
        SolidSenseLed.init = True

    @staticmethod
    def set(led, level):
        if level < 0 or level > 255:
            raise ValueError("Invalid LED level")

        l = ("%d" % level).encode()
        fd = open(led, "bw")
        fd.write(l)
        fd.close()

    @staticmethod
    def ledref(led_num):
        if not SolidSenseLed.init:
            SolidSenseLed.detect_leds()
        # print("Setting LED",led_num)
        try:
            led = SolidSenseLed.leds[led_num]
        except IndexError:
            return None
        if led is not None:
            return led.impl()
        else:
            raise ValueError("No led " + str(led_num))


class LedWrapper:
    def __init__(self, index):
        self._colors = {}
        self._index = index

    def add_color(self, color):
        if color == "red":
            self._colors["red"] = True
        elif color == "green":
            self._colors["green"] = True
        else:
            raise ValueError("Unknown color")

    def impl(self):
        def fullname(self, color):
            exists = self._colors[color]
            file = "%s/%s%1d/brightness" % (SolidSenseLed.led_path, color, self._index)
            # print(file)
            return file

        if len(self._colors) > 1:
            led = BicolorLed(fullname(self, "red"), fullname(self, "green"))
        else:
            led = MonochromeLed(fullname(self, "green"))
        return led


# ############################################################################


class BaseLed:
    def off(self):
        pass

    def on(self, level):
        pass

    def in_progress(self):
        pass

    def ok(self):
        pass


class AbstractLed(BaseLed):
    def __init__(self):
        self._timer = None

    def off(self):
        self._stop_timer()

    def _switch_blink(self):
        if self._cur_i == self._blink_min:
            self._cur_i = self._blink_max
        else:
            self._cur_i = self._blink_min
        SolidSenseLed.set(self._blink_led, self._cur_i)
        self._arm_timer(self._switch_blink)

    def _arm_timer(self, callback):
        self._timer = threading.Timer(self._period, callback)
        self._timer.start()

    def _stop_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


class MonochromeLed(AbstractLed):
    def __init__(self, file):
        self._file = file
        super().__init__()

        self._level = 0

    def on(self, level):
        SolidSenseLed.set(self._file, level)
        self._level = level

    def off(self):
        super().off()
        SolidSenseLed.set(self._file, 0)
        self._level = 0

    def blink(self, min_i, max_i, period):
        self._cur_i = min_i
        self._blink_min = min_i
        self._blink_max = max_i
        self._period = period
        self._blink_led = self._file
        SolidSenseLed.set(self._file, min_i)
        self._arm_timer(self._switch_blink)

    def stop_blink(self):
        self._stop_timer()
        self.on(self._level)

    def in_progress(self):
        self.blink(0, 255, 0.5)

    def ok(self):
        self.stop_blink()
        self.on(255)


class BicolorLed(AbstractLed):
    def __init__(self, file_red, file_green):
        self._r = file_red
        self._g = file_green
        self._glevel = 0
        self._rlevel = 0
        super().__init__()

    def green(self, level):
        SolidSenseLed.set(self._g, level)
        self._glevel = level

    def red(self, level):
        SolidSenseLed.set(self._r, level)
        self._rlevel = level

    def green_only(self, level):
        super().off()
        self.red(0)
        self.green(level)

    def red_only(self, level):
        super().off()
        self.red(level)
        self.green(0)

    def off(self):
        super().off()
        SolidSenseLed.set(self._g, 0)
        SolidSenseLed.set(self._r, 0)
        self._glevel = 0
        self._rlevel = 0

    def on(self, level):
        self.red_only(level)

    def _switch_color(self):
        l = self._blink_led
        self._blink_led = self._off_led
        self._off_led = l
        SolidSenseLed.set(self._blink_led, self._blink_max)
        SolidSenseLed.set(self._off_led, 0)
        self._arm_timer(self._switch_color)

    def blink_red(self, min_i, max_i, period):
        self._blink_min = min_i
        self._blink_max = max_i
        self._period = period
        SolidSenseLed.set(self._g, 0)
        SolidSenseLed.set(self._r, min_i)
        self._blink_led = self._r
        self._cur_i = min_i
        self._arm_timer(self._switch_blink)

    def blink_green(self, min_i, max_i, period):
        self._blink_min = min_i
        self._blink_max = max_i
        self._period = period
        SolidSenseLed.set(self._r, 0)
        SolidSenseLed.set(self._g, min_i)
        self._blink_led = self._g
        self._cur_i = min_i
        self._arm_timer(self._switch_blink)

    def blink_red_green(self, period, level):
        self._blink_max = level
        self._period = period
        self._blink_led = self._r
        self._off_led = self._g
        SolidSenseLed.set(self._blink_led, self._blink_max)
        SolidSenseLed.set(self._off_led, 0)
        self._arm_timer(self._switch_color)

    def stop_blink(self):
        self._stop_timer()
        SolidSenseLed.set(self._g, self._glevel)
        SolidSenseLed.set(self._r, self._rlevel)

    def in_progress(self):
        self.red_only(255)

    def ok(self):
        self.green_only(255)


# ############################################################################


def test():

    SolidSenseLed.detect_leds()

    # SolidSenseLed.led1(SolidSenseLed.GREEN,255)
    # SolidSenseLed.led2(SolidSenseLed.RED,255)
    led = SolidSenseLed.ledref(2)
    led.blink_red(0, 255, 0.5)
    time.sleep(10.0)
    led.stop_blink()
    time.sleep(3.0)
    led.blink_green(0, 255, 0.5)
    time.sleep(10.0)
    led.stop_blink()
    time.sleep(1.0)
    led.blink_red_green(0.5, 255)
    time.sleep(10.0)
    led.stop_blink()


if __name__ == "__main__":
    test()
