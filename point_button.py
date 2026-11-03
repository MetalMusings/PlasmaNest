"""Save a sheet corner from a button on a pendant or gamepad, like the QtPlasmaC Spara punkt button.

Load it from a postgui HAL file, without -W, so a failure here never stops LinuxCNC from starting:

    loadusr python3 /path/to/plasmanest/point_button.py input.0.btn-start

It registers the HAL component "plasmanest" (needed to read pins), reads the given bit pin
20 times a second and saves a point each time the button goes down. It does not link or
change any pin. Errors are shown in QtPlasmaC.
"""

import os
import signal
import sys
import time

POLL_SECONDS = 0.05
COMPONENT = "plasmanest"


class ButtonWatcher:
    """Calls save once per press: on the change from released to pressed."""

    def __init__(self, read, save):
        self.read, self.save = read, save
        self.was_down = True  # a button held at start-up is not a press

    def step(self):
        down = bool(self.read())
        pressed = down and not self.was_down
        self.was_down = down
        if pressed:
            self.save()
        return pressed


def report_error(text):
    try:
        import linuxcnc
        linuxcnc.command().error_msg(f"PlasmaNest: {text}")
    except Exception:
        print(f"PlasmaNest: {text}", file=sys.stderr)


def save_point():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import linuxcnc
        import save_point as script
        stat = linuxcnc.stat()
        stat.poll()
        script.save(stat)
    except Exception as error:
        report_error(error)


def main(argv):
    if len(argv) != 2:
        print("Användning: point_button.py <bit-pinne>, till exempel input.0.btn-start", file=sys.stderr)
        return 2
    pin = argv[1]
    import hal
    try:
        component = hal.component(COMPONENT)
    except Exception as error:
        report_error(f"Kan inte skapa HAL-komponenten {COMPONENT} (körs den redan?): {error}")
        return 1
    component.ready()
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
    try:
        hal.get_value(pin)
    except Exception as error:
        report_error(f"Kan inte läsa {pin}: {error}")
        component.exit()
        return 1
    watcher = ButtonWatcher(lambda: hal.get_value(pin), save_point)
    try:
        while True:
            watcher.step()
            time.sleep(POLL_SECONDS)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        component.exit()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
