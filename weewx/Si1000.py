#
#    Copyright (c) 2013 Andrew Tridgell
#
#    See the file LICENSE.txt for your full rights.
#
#
"""Driver for Si1000 radio receiver for a Davis weather station

See https://github.com/tridge/DavisSi1000 for details of the radio
receiver used

Driver based on simulator.py written by Tom Keffer

To use this driver, add the following to weewx.conf:

[Station]
    station_type = Si1000

[Si1000]
    driver = weewx.drivers.Si1000
    port = /dev/ttyAMA0
    baudrate = 57600

    # adjust wind direction based on install orientation
    wind_dir_adjust = 180

"""

from __future__ import with_statement
import math
import time
import json
import os

import weedb
import weeutil.weeutil
#import weewx.abstractstation
import weewx.wxformulas
import weewx.drivers

def loader(config_dict, engine):
    station = Si1000(**config_dict['Si1000'])
    return station
        
class Si1000(weewx.drivers.AbstractDevice):

    """Si1000 driver"""
    
    def __init__(self, **stn_dict):
        """Initialize the driver
        NAMED ARGUMENTS:
        
        port: serial port path or log directory. If this is a directory then the driver will read log files from that directory instead of directly from a radio

        baudrate: baud rate for serial port
        
        wind_dir_adjust: wind direction adjustment in degrees

        rawlogdir: directory to put daily raw logs (directory must exist)
        """
        import serial
        
        self.port = stn_dict.get('port', '/dev/ttyAMA0')
        self.baudrate = int(stn_dict.get('baudrate', 57600))
        self.wind_dir_adjust = int(stn_dict.get('wind_dir_adjust', 180))
        self.rawlogdir = stn_dict.get('rawlogdir', None)
        if os.path.isdir(self.port):
            self.directory_mode = True
        else:
            self.fd = serial.Serial(self.port, self.baudrate, timeout=3)
            self.directory_mode = False
        
        self.last_rain = None

        '''
        a mapping between field names on the Si1000 and the weewx field names. The extra
        parameter is an optional conversion function
        '''
        self.fieldmap = {
            'wind_direction_degrees' : ('windDir',   self.adjust_wind_direction),
            'wind_speed_mph'         : ('windSpeed', None),
            'temperature_F'          : ('outTemp',   None),
            'humidity_pct'           : ('humidity',  None),
            'rain_spoons'            : ('rain',      self.convert_rain),
            'timestamp_utc'          : ('dateTime',  None)
            }

    def log_rawdata(self, values):
        '''log some raw data'''
        if self.rawlogdir is None:
            return
        # we re-open each time to allow for rsync transfer of the data to a remote
        # host. When transferred the log is deleted
        rawpath = os.path.join(self.rawlogdir, time.strftime('%Y%m%d.log'))
        rawfile = open(rawpath, mode='a')
        rawfile.write("%s\n" % json.dumps(values))
        rawfile.close()

    def convert_rain(self, rain_spoons):
        '''convert rain_spoons to rain in inches'''
        if self.last_rain is None:
            self.last_rain = rain_spoons
            return 0
        ret = rain_spoons - self.last_rain
        if ret < 0:
            # rain_spoons is 7 bit
            ret = 128 + ret
        self.last_rain = rain_spoons
        # each spoon is 0.1"
        return ret * 0.1

    def adjust_wind_direction(self, direction):
        '''adjust wind direction for installation'''
        # direction = (int(time.time())//4) % 360
        direction += self.wind_dir_adjust
        if direction > 360:
            direction -= 360
        return direction

    def process_line(self, line):
        '''process a line of input'''
        if line[0] != '{' or line[-1] != '}':
            return None
        
        values = json.loads(line)
        # if the data is being read from a logfile it will already contain
        # timestamp_utc fields with the original capture time
        if 'timestamp_utc' not in values:
            values['timestamp_utc'] = int(time.time()+0.5)

        self.log_rawdata(values)

        packet = { 'usUnits' : weewx.US }

        for k in values.keys():
            if k in self.fieldmap:
                (name, conversion) = self.fieldmap[k]
                value = values[k]
                if conversion is not None:
                    value = conversion(value)
                packet[name] = value

        return packet

    def genLoopPackets_device(self):
        '''generate packets from the radio'''
        while True:
            line = self.fd.readline().decode('ascii')
            line = line.strip()
            if not line:
                time.sleep(0.1)
                continue
            packet = self.process_line(line)
            if packet is None:
                time.sleep(0.1)
                continue
            yield packet


    def genLoopPackets_directory(self):
        '''generate packets from a log directory, deleting files as they are processed'''
        time.sleep(1)
        for root, dirs, files in os.walk(self.port):
            for filename in sorted(files):
                path = os.path.join(root, filename)
                if filename[0] == '.':
                    continue
                file = open(path, mode='r')
                for line in file:
                    line = line.strip()
                    if not line:
                        continue
                    packet = self.process_line(line)
                    if packet is None:
                        continue
                    if packet['dateTime'] + 600 > time.time():
                        time.sleep(1)
                    yield packet
                file.close()
                os.unlink(path)

    def genArchiveRecords(self, since_ts):
        '''Generator function that returns archive records'''
        if not self.directory_mode:
            raise NotImplementedError("Method 'genArchiveRecords' not implemented")
        for packet in self.genLoopPackets_directory():
            if packet['dateTime'] > since_ts:
                yield packet

    def genLoopPackets(self):
        '''generate packets from the logs or radio'''
        if self.directory_mode:
            while True:
                for packet in self.genLoopPackets_directory():
                    yield packet
        else:
            for packet in self.genLoopPackets_device():
                yield packet
                
                
    @property
    def hardware_name(self):
        return "DavisSi1000"
        
if __name__ == "__main__":

    station = Si1000()
    for packet in station.genLoopPackets():
        print(weeutil.weeutil.timestamp_to_string(packet['dateTime']), packet)
