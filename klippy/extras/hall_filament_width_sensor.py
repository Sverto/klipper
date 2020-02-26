# Support for filament width sensor
#
# Copyright (C) 2019  Mustafa YILDIZ <mydiz@hotmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import filament_switch_sensor

ADC_REPORT_TIME = 0.500
ADC_SAMPLE_TIME = 0.001
ADC_SAMPLE_COUNT = 5

class HallFilamentWidthSensor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.pin1 = config.get('adc1')
        self.pin2 = config.get('adc2')
        self.dia1=config.getfloat('Cal_dia1', 1.5)
        self.dia2=config.getfloat('Cal_dia2', 2.0)
        self.rawdia1=config.getint('Raw_dia1', 9500)
        self.rawdia2=config.getint('Raw_dia2', 10500)
        self.MEASUREMENT_INTERVAL_MM=config.getint('measurement_interval',10)
        self.nominal_filament_dia = config.getfloat(
            'default_nominal_filament_diameter', above=1)
        self.measurement_delay = config.getfloat('measurement_delay', above=0.)
        self.measurement_max_difference = config.getfloat('max_difference', 0.2)
        self.max_diameter = (self.nominal_filament_dia
                             + self.measurement_max_difference)
        self.min_diameter = (self.nominal_filament_dia
                             - self.measurement_max_difference)
        self.diameter =self.nominal_filament_dia
        self.is_active =config.getboolean('enable', False)
        self.runout_dia=config.getfloat('min_diameter', 1.0)
        # filament array [position, filamentWidth]
        self.filament_array = []
        self.lastFilamentWidthReading = 0
        # printer objects
        self.toolhead = self.ppins = self.mcu_adc = None
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        # Start adc
        self.ppins = self.printer.lookup_object('pins')
        self.mcu_adc = self.ppins.setup_pin('adc', self.pin1)
        self.mcu_adc.setup_minmax(ADC_SAMPLE_TIME, ADC_SAMPLE_COUNT)
        self.mcu_adc.setup_adc_callback(ADC_REPORT_TIME, self.adc_callback)
        self.mcu_adc2 = self.ppins.setup_pin('adc', self.pin2)
        self.mcu_adc2.setup_minmax(ADC_SAMPLE_TIME, ADC_SAMPLE_COUNT)
        self.mcu_adc2.setup_adc_callback(ADC_REPORT_TIME, self.adc2_callback)
        # extrude factor updating
        self.extrude_factor_update_timer = self.reactor.register_timer(
            self.extrude_factor_update_event)
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('QUERY_FILAMENT_WIDTH', self.cmd_M407)
        self.gcode.register_command('RESET_FILAMENT_WIDTH_SENSOR',
                                    self.cmd_ClearFilamentArray)
        self.gcode.register_command('DISABLE_FILAMENT_WIDTH_SENSOR',
                                    self.cmd_M406)
        self.gcode.register_command('ENABLE_FILAMENT_WIDTH_SENSOR',
                                    self.cmd_M405)
        self.gcode.register_command('QUERY_RAW_FILAMENT_WIDTH',
                                    self.cmd_Get_Raw_Values)
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
    # Initialization
    def handle_ready(self):
        # Load printer objects
        self.toolhead = self.printer.lookup_object('toolhead')

        # Start extrude factor update timer
        self.reactor.update_timer(self.extrude_factor_update_timer,
                                  self.reactor.NOW)

    def adc_callback(self, read_time, read_value):
        # read sensor value
        self.lastFilamentWidthReading = round(read_value * 10000)

    def adc2_callback(self, read_time, read_value):
        # read sensor value
        self.lastFilamentWidthReading2 = round(read_value * 10000)
        # calculate diameter
        self.diameter = round((self.dia2 - self.dia1)/
            (self.rawdia2-self.rawdia1)*
          ((self.lastFilamentWidthReading+self.lastFilamentWidthReading2)
           -self.rawdia1)+self.dia1,2)

    def update_filament_array(self, last_epos):
        # Fill array
        if len(self.filament_array) > 0:
            # Get last reading position in array & calculate next
            # reading position
          next_reading_position = (self.filament_array[-1][0] +
          self.MEASUREMENT_INTERVAL_MM)
          if next_reading_position <= (last_epos + self.measurement_delay):
            self.filament_array.append([last_epos + self.measurement_delay,
                                            self.diameter])
        else:
            # add first item to array
            self.filament_array.append([self.measurement_delay + last_epos,
                                        self.diameter])

    def extrude_factor_update_event(self, eventtime):
        # Update extrude factor
        pos = self.toolhead.get_position()
        last_epos = pos[3]
        # Update filament array for lastFilamentWidthReading
        self.update_filament_array(last_epos)
        # Check runout
        self.runout_helper.note_filament_present(
            self.diameter > self.runout_dia)
        # Does filament exists
        if self.lastFilamentWidthReading > 0.5:
            if len(self.filament_array) > 0:
                # Get first position in filament array
                pending_position = self.filament_array[0][0]
                if pending_position <= last_epos:
                    # Get first item in filament_array queue
                    item = self.filament_array.pop(0)
                    filament_width = item[1]
                    if ((filament_width <= self.max_diameter)
                        and (filament_width >= self.min_diameter)):
                        percentage = round(self.nominal_filament_dia**2
                                           / filament_width**2 * 100)
                        self.gcode.run_script("M221 S" + str(percentage))
                    else:
                        self.gcode.run_script("M221 S100")
        else:
            self.gcode.run_script("M221 S100")
            self.filament_array = []
        return eventtime + 1

    def cmd_M407(self, params):
        response = ""
        if self.lastFilamentWidthReading > 0:
            response += ("Filament dia (measured mm): "
                         + str(self.diameter))
        else:
            response += "Filament NOT present"
        self.gcode.respond(response)

    def cmd_ClearFilamentArray(self, params):
        self.filament_array = []
        self.gcode.respond("Filament width measurements cleared!")
        # Set extrude multiplier to 100%
        self.gcode.run_script_from_command("M221 S100")

    def cmd_M405(self, params):
        response = "Filament width sensor Turned On"
        if self.is_active:
            response = "Filament width sensor is already On"
        else:
            self.is_active = True
            # Start extrude factor update timer
            self.reactor.update_timer(self.extrude_factor_update_timer,
                                      self.reactor.NOW)
        self.gcode.respond(response)

    def cmd_M406(self, params):
        response = "Filament width sensor Turned Off"
        if not self.is_active:
            response = "Filament width sensor is already Off"
        else:
            self.is_active = False
            # Stop extrude factor update timer
            self.reactor.update_timer(self.extrude_factor_update_timer,
                                      self.reactor.NEVER)
            # Clear filament array
            self.filament_array = []
            # Set extrude multiplier to 100%
            self.gcode.run_script_from_command("M221 S100")
        self.gcode.respond(response)

    def cmd_Get_Raw_Values(self, params):
        response = "ADC1="
        response +=  (" "+str(self.lastFilamentWidthReading))
        response +=  (" ADC2="+str(self.lastFilamentWidthReading2))
        response +=  (" RAW="+
                      str(self.lastFilamentWidthReading
                      +self.lastFilamentWidthReading2))
        self.gcode.respond(response)
    def get_status(self, eventtime):
        return {'Diameter': self.diameter,
                'Raw':(self.lastFilamentWidthReading+
                 self.lastFilamentWidthReading2),
                'is_active':self.is_active}

def load_config(config):
    return HallFilamentWidthSensor(config)