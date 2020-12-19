"""
Referenced from code written by Dave Astels for Adafruit Industries
"""

#pylint:disable=redefined-outer-name,no-member,global-statement
#pylint:disable=no-self-use,too-many-branches,too-many-statements
#pylint:disable=useless-super-delegation, too-many-locals

import time
import json 
from secrets import secrets
import board
from adafruit_pyportal import PyPortal
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label
from digitalio import DigitalInOut, Direction, Pull
import analogio
import displayio
import adafruit_logging as logging
import adafruit_touchscreen


# Set up where we'll be fetching data from
DATA_SOURCE = "https://www.adafruit.com/api/quotes.php"
QUOTE_LOCATION = [0, 'text']
AUTHOR_LOCATION = [0, 'author']


####################
# setup hardware

pyportal = PyPortal(url=DATA_SOURCE,
                    json_path=(QUOTE_LOCATION, AUTHOR_LOCATION),
                    status_neopixel=board.NEOPIXEL,
                    text_font="fonts/Arial-ItalicMT-17.bdf",
                    text_position=((30, 170),  # quote location
                                   (30, 190)), # author location
                    text_color=(0xFFFFFF,  # quote text color
                                0x8080FF), # author text color
                    text_wrap=(0, # characters to wrap for quote
                               0), # no wrap for author
                    text_maxlen=(180, 30), # max text size for quote & author
                    )


snooze_button = DigitalInOut(board.D3)
snooze_button.direction = Direction.INPUT
snooze_button.pull = Pull.UP

screen_width = 360
screen_height = 240
ts = adafruit_touchscreen.Touchscreen(board.TOUCH_XL, board.TOUCH_XR,
                                      board.TOUCH_YD, board.TOUCH_YU,
                                      calibration=((5200, 59000), (5800, 57000)),
                                      size=(320, 240))

####################
# variables

# alarm support

alarm_background = 'red_alert.bmp'
alarm_file = 'alarm.wav'
alarm_enabled = True
alarm_armed = True
alarm_interval = 10.0
alarm_hour = 9
alarm_minute = 45
snooze_time = None
snooze_interval = 600.0

# timer mode bg
timer_background = 'clock_background.bmp'

# weather support

icon_file = None
icon_sprite = None


# display/data refresh timers

refresh_time = None
update_time = None

# The most recently fetched time
current_time = None


####################
# Load the fonts

time_font = bitmap_font.load_font('/fonts/Anton-Regular-104.bdf')
time_font.load_glyphs(b'0123456789:') # pre-load glyphs for fast printing

alarm_font = bitmap_font.load_font('/fonts/Helvetica-Bold-36.bdf')
alarm_font.load_glyphs(b'0123456789:')

date_font = bitmap_font.load_font('/fonts/Arial-16.bdf')
date_font.load_glyphs(b'0123456789CF')

small_font = bitmap_font.load_font("fonts/Arial-ItalicMT-17.bdf")

####################
# Set up logging

logger = logging.getLogger('alarm_clock')
logger.setLevel(logging.ERROR)            # change as desired

####################
# Functions

def create_text_areas(configs):
    """Given a list of area specifications, create and return test areas."""
    text_areas = []
    for cfg in configs:
        textarea = Label(cfg['font'], text=' '*cfg['size'])
        textarea.x = cfg['x']
        textarea.y = cfg['y']
        textarea.color = cfg['color']
        text_areas.append(textarea)
    return text_areas


def clear_splash():
    for _ in range(len(pyportal.splash) - 1):
        pyportal.splash.pop()


def touch_in_button(t, b):
    in_horizontal = b['left'] <= t[0] <= b['right']
    in_vertical = b['top'] <= t[1] <= b['bottom']
    return in_horizontal and in_vertical


touched = False

####################
# states

class State(object):
    """State abstract base class"""

    def __init__(self):
        pass


    @property
    def name(self):
        """Return the name of teh state"""
        return ''


    def tick(self, now):
        """Handle a tick: one pass through the main loop"""
        pass


    #pylint:disable=unused-argument
    def touch(self, t, touched):
        """Handle a touch event.
        :param (x, y, z) - t: the touch location/strength"""
        return bool(t)


    def enter(self):
        """Just after the state is entered."""
        pass


    def exit(self):
        """Just before the state exits."""
        clear_splash()


class Time_State(State):
    """This state manages the primary time display screen/mode"""

    def __init__(self):
        super().__init__()
        self.background_day = 'clock_background.bmp'
        self.background_night = 'clock_background.bmp'
        self.refresh_time = None
        self.update_time = None
        text_area_configs = [dict(x=55, y=95, size=4, color=0x000000, font=time_font),
                             dict(x=220, y=20, size=5, color=0x00FF00, font=date_font),
                             dict(x=30, y=20, size=5, color=0xFFFFFF, font=date_font),
                             dict(x=5, y=screen_height-62, size=50, color=0xFFFFFF, font=small_font, text_wrap=0),]
        self.text_areas = create_text_areas(text_area_configs)
        self.weather_icon = displayio.Group()
        self.weather_icon.x = 88
        self.weather_icon.y = 20
        self.icon_file = None

        self.snooze_icon = displayio.Group()
        self.snooze_icon.x = 260
        self.snooze_icon.y = 70
        self.snooze_file = None

        # each button has it's edges as well as the state to transition to when touched
        self.buttons = [dict(left=0, top=screen_height-50, right=int(screen_width/2), bottom=screen_height, next_state='settings'),
                        dict(left=int(screen_width/2), top=screen_height-50, right=screen_width, bottom=screen_height, next_state='timer')]


    @property
    def name(self):
        return 'time'


    def tick(self, now):
        global alarm_armed, snooze_time, update_time, current_time

        # is the snooze button pushed? Cancel the snooze if so.
        if not snooze_button.value:
            if snooze_time:
                self.snooze_icon.pop()
            snooze_time = None
            alarm_armed = False

        # is snooze active and the snooze time has passed? Transition to alram is so.
        if snooze_time and ((now - snooze_time) >= snooze_interval):
            change_to_state('alarm')
            return


        # only query the online time once per hour (and on first run)
        if (not self.refresh_time) or ((now - self.refresh_time) > 3600):
            logger.debug('Fetching time')
            try:
                pyportal.get_local_time(location=secrets['timezone'])
                self.refresh_time = now
            except RuntimeError as e:
                self.refresh_time = now - 3000   # delay 10 minutes before retrying
                logger.error('Some error occured, retrying! - %s', str(e))


        if (not update_time) or ((now - update_time) > 30):
            # Update the time
            update_time = now
            current_time = time.localtime()
            time_string = '%02d %02d' % (current_time.tm_hour,current_time.tm_min)
            self.text_areas[0].text = time_string
            try:
                board.DISPLAY.refresh(target_frames_per_second=60)
            except AttributeError:
                board.DISPLAY.refresh_soon()
                board.DISPLAY.wait_for_frame()


            # Check if alarm should sound
        if current_time is not None and not snooze_time:
            minutes_now = current_time.tm_hour * 60 + current_time.tm_min
            minutes_alarm = alarm_hour * 60 + alarm_minute
            if minutes_now == minutes_alarm:
                if alarm_armed:
                    change_to_state('alarm')
            else:
                alarm_armed = alarm_enabled


    def touch(self, t, touched):
        if t and not touched:             # only process the initial touch
            for button_index in range(len(self.buttons)):
                b = self.buttons[button_index]
                if touch_in_button(t, b):
                    change_to_state(b['next_state'])
                    break
        return bool(t)


    def enter(self):
        global quote_value
        pyportal.set_background('clock_background.bmp')
        print('entering home screen')
        
        for ta in self.text_areas:
            pyportal.splash.append(ta)
        if snooze_time:
            if self.snooze_file:
                self.snooze_file.close()
            self.snooze_file = open('/icons/zzz.bmp', "rb")
            icon = displayio.OnDiskBitmap(self.snooze_file)

            self.snooze_icon.append(icon_sprite)
            pyportal.splash.append(self.snooze_icon)
        self.text_areas[2].text = '%2d/%02d' % (time.localtime().tm_mon, time.localtime().tm_mday)
        
        # try:
        #     quote_value = pyportal.fetch()
        #     print("Response quote is", quote_value)
        # except (ValueError, RuntimeError) as e:
        #     print("Some error occured, retrying! -", e)

        # self.text_areas[3].text = quote_value
        self.text_areas[3].text = "Uneasy lies the head that wears a crown."

        if alarm_enabled:
            self.text_areas[1].text = '%2d:%02d' % (alarm_hour, alarm_minute)
        else:
            self.text_areas[1].text = '     '
        try:
            board.DISPLAY.refresh(target_frames_per_second=60)
        except AttributeError:
            board.DISPLAY.refresh_soon()
            board.DISPLAY.wait_for_frame()

    def exit(self):
        super().exit()
        for _ in range(len(self.snooze_icon)):
            self.snooze_icon.pop()


class Timer_State(State):
    """This state lets the user enable/disable the alarm and set its time.
    Swiping up/down adjusts the hours & miniutes separately."""

    def __init__(self):
        super().__init__()
        self.previous_touch = None
        # self.background = 'settings_background.bmp'
        self.background = 'timer_background.bmp'

        self.start_time = time.monotonic()
        self.timer_length = 1500
        self.pre_sec = None
        self.timer_activated = False
        self.elpased_time = int(time.monotonic() - self.start_time)
        self.remaining_time = self.timer_length - self.elpased_time
        self.minutes_rem = int(self.remaining_time/60)
        self.second_rem = self.remaining_time%60
        
        text_area_configs = [dict(x=40, y=80, size=4, color=0xFFFFFF, font=time_font)]

        self.text_areas = create_text_areas(text_area_configs)
        self.buttons = [dict(left=0, top=screen_height-50, right=int(screen_width/2),       bottom=screen_height, next_state='time'),
                        dict(left=int(screen_width/2), top=screen_height-50, right=screen_width, bottom=screen_height, next_state='timer'),
                        dict(left=0, right=screen_width, top=screen_height-100, bottom=screen_height-50)]


    @property
    def name(self):
        return 'settings'


    def touch(self, t, touched):
        global alarm_hour, alarm_minute, alarm_enabled
        if t:
            if touch_in_button(t, self.buttons[0]):   # on
                change_to_state('time')
                
            elif touch_in_button(t, self.buttons[2]):
                self.timer_activated = True
                self.text_areas[0].text = '%02d:%02d' % (self.minutes_rem, self.second_rem)
       
            try:
                board.DISPLAY.refresh(target_frames_per_second=60)
            except AttributeError:
                board.DISPLAY.refresh_soon()
                board.DISPLAY.wait_for_frame()

        else:
            self.previous_touch = None
        return bool(t)


    def enter(self):
        print('entering settings')

        pyportal.set_background(self.background)
        for ta in self.text_areas:
            pyportal.splash.append(ta)


        if self.timer_activated:
            if self.second_rem != self.pre_sec:
                self.pre_sec = self.second_rem
                print(self.minutes_rem, self.second_rem)

        
        self.text_areas[0].text = '%02d:%02d' % (self.minutes_rem, self.second_rem) # set time textarea





class Alarm_State(State):
    """This state shows/sounds the alarm.
    Touching anywhere on the screen cancells the alarm.
    Pressing the snooze button turns of the alarm, starting it again in 10 minutes."""

    def __init__(self):
        super().__init__()
        self.sound_alarm_time = None


    @property
    def name(self):
        return 'alarm'


    def tick(self, now):
        global snooze_time

        # is the snooze button pushed
        if not snooze_button.value:
            snooze_time = now
            change_to_state('time')
            return

        # is it time to sound the alarm?
        if self.sound_alarm_time and (now - self.sound_alarm_time) > alarm_interval:
            self.sound_alarm_time = now
            pyportal.play_file(alarm_file)


    def touch(self, t, touched):
        global snooze_time
        if t and not touched:
            snooze_time = None
            change_to_state('time')
        return bool(t)

    def enter(self):
        self.sound_alarm_time = time.monotonic() - alarm_interval
        
        pyportal.set_background(alarm_background)
        try:
            board.DISPLAY.refresh(target_frames_per_second=60)
        except AttributeError:
            board.DISPLAY.refresh_soon()
            board.DISPLAY.wait_for_frame()


    def exit(self):
        global alarm_armed
        super().exit()
        alarm_armed = bool(snooze_time)


class Setting_State(State):
    """This state lets the user enable/disable the alarm and set its time.
    Swiping up/down adjusts the hours & miniutes separately."""

    def __init__(self):
        super().__init__()
        self.previous_touch = None
        # self.background = 'settings_background.bmp'
        self.background = 'settings_background.bmp'
        
        text_area_configs = [dict(x=80, y=100, size=4, color=0xFFFFFF, font=time_font)]

        self.text_areas = create_text_areas(text_area_configs)
        self.buttons = [
                        dict(left=0, top=30, right=80, bottom=93),    # on
                        dict(left=0, top=98, right=80, bottom=152),   # return
                        dict(left=320, top=0, right=360, bottom=40),  # off
                        dict(left=100, top=0, right=200, bottom = 240), # hours
                        dict(left=220, top=0, right=320, bottom = 240),
                        dict(left=0, top=200, right=120, bottom=240, next_state='settings'), # Navigate to settings
                        dict(left=120, top=200, right=240, bottom=240, next_state='timer')]   # Navigate to timer

    @property
    def name(self):
        return 'settings'


    def touch(self, t, touched):
        global alarm_hour, alarm_minute, alarm_enabled
        if t:
            if touch_in_button(t, self.buttons[0]):   # on
                logger.debug('ON touched')
                alarm_enabled = True
                self.text_areas[0].text = '%02d:%02d' % (alarm_hour, alarm_minute)
            elif touch_in_button(t, self.buttons[1]):   # return
                logger.debug('RETURN touched')
                change_to_state('time')
            elif touch_in_button(t, self.buttons[2]): # off
                logger.debug('OFF touched')
                alarm_enabled = False
                pyportal.set_background(0x0000FF, position=None)
                self.text_areas[0].text = '     '
            elif touch_in_button(t, self.buttons[5]):
                change_to_state('time')
            elif alarm_enabled:
                if not self.previous_touch:
                    self.previous_touch = t
                else:
                    if touch_in_button(t, self.buttons[3]):   # HOURS
                        logger.debug('HOURS touched')
                        if t[1] < (self.previous_touch[1] - 5):   # moving up
                            alarm_hour = (alarm_hour + 1) % 24
                            logger.debug('Alarm hour now: %d', alarm_hour)
                        elif t[1] > (self.previous_touch[1] + 5): # moving down
                            alarm_hour = (alarm_hour - 1) % 24
                            logger.debug('Alarm hour now: %d', alarm_hour)
                        self.text_areas[0].text = '%02d:%02d' % (alarm_hour, alarm_minute)
                    elif touch_in_button(t, self.buttons[4]): # MINUTES
                        logger.debug('MINUTES touched')
                        if t[1] < (self.previous_touch[1] - 5):   # moving up
                            alarm_minute = (alarm_minute + 1) % 60
                            logger.debug('Alarm minute now: %d', alarm_minute)
                        elif t[1] > (self.previous_touch[1] + 5): # moving down
                            alarm_minute = (alarm_minute - 1) % 60
                            logger.debug('Alarm minute now: %d', alarm_minute)
                        self.text_areas[0].text = '%02d:%02d' % (alarm_hour, alarm_minute)
                    self.previous_touch = t
            try:
                board.DISPLAY.refresh(target_frames_per_second=60)
            except AttributeError:
                board.DISPLAY.refresh_soon()
                board.DISPLAY.wait_for_frame()

        else:
            self.previous_touch = None
        return bool(t)


    def enter(self):
        global snooze_time
        snooze_time = None
        print('entering settings')

        pyportal.set_background(self.background)
        for ta in self.text_areas:
            pyportal.splash.append(ta)
        if alarm_enabled:
            self.text_areas[0].text = '%02d:%02d' % (alarm_hour, alarm_minute) # set time textarea
        else:
            self.text_areas[0].text = '     '


####################
# State management

states = {'time': Time_State(),
          'timer': Timer_State(),
          'alarm': Alarm_State(),
          'settings': Setting_State()}

current_state = None


def change_to_state(state_name):
    global current_state
    if current_state:
        logger.debug('Exiting %s', current_state.name)
        current_state.exit()
    current_state = states[state_name]
    logger.debug('Entering %s', current_state.name)
    current_state.enter()



####################
# And... go

clear_splash()
change_to_state("time")

# pyportal.preload_font()

while True:
    touched = current_state.touch(pyportal.touchscreen.touch_point, touched)
    current_state.tick(time.monotonic())
