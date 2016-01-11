'''
Created on Jun 17, 2015

@author: lele
'''
import os
import logger as l
import copy
import time
import datetime
import special_bidir_traffic_checker as sbtc
import read_write_config_file as rwcf

import subprocess
import invoke as invoke

#required for loading DatabaseHandler class
import sys
sys.path.append("db/")
from database_adapter import SQLiteDatabaseAdapter

class ReadConfig(object):
    '''
    This class is devoted to read the config file
    '''

    '''
    Besides nfpa.cfg file, some additional info and variables
    Additional info for storing some config vars:
        trafficTypes : list [simple,tr2e, etc.]
        packetSizes : list [64,128, etc.]
        realisticTraffics : list [nick names of pcap file, e.g., wifi.
        In case of 'wifi', a PCAP file named nfpa.wifi.pcap should be placed
        in MAIN_ROOT/PCAP folder, since nfpa will look after the file
        in this manner!
        LOG_PATH String - for storing where to save log files, which is set to
        MAIN_ROOT/log
        app_start_date String - unix timestamp as String storing the start of 
        the application
    '''
    
    
    


    def __init__(self):
        '''
        Constructor
        read_only Bool - if read_only is False, this class automatically 
        continues processing (calls other functions) after reading config file.
        This is useful if CLI is used, and config file has been properly edited.
        If Web-based GUI is also enabled, only reading is allowed, then we
        should stop processing, since this is reading is only needed to fill up
        web-form for configuration with preset values. In this case, web-based
        form will automatically creates/updates config file and instantiated a
        new class from this with read_only=False set
          
        '''
        
        #dictionary for storing configuration parameters read from config file
        self._config = {}
        #read config
        tmp_cfg = rwcf.readConfigFile("nfpa.cfg")
        #check whether it was successful
        if tmp_cfg[0] == True:
            self._config = tmp_cfg[1]
        else:
            print(tmp_cfg[1])
            exit(-1)
            
        
        #create a list of dictionary indexes for easier iterating though data
        #actually, these are the measured data units/names stored and placed in
        #gnuplot file as well, therefore iterating through this dictionary eases
        #the code via not accessing the fields explicitly
        #sp - sent pps, rb - recv bps, etc.
        self._config['header_uni'] = ['sent_pps', 'recv_pps', 'miss_pps', 
                                      'sent_bps', 'recv_bps', 'diff_bps']
        
        self._config['header_bi']  = ['sent_pps_bidir', 'recv_pps_bidir', 'miss_pps_bidir',
                                      'sent_bps_bidir', 'recv_bps_bidir', 'diff_bps_bidir']
        
        self._config['helper_header'] = ['min', 'avg', 'max']
 
                  
        self.log = l.getLogger( self.__class__.__name__, 
                                self._config['LOG_LEVEL'], 
                                self._config['app_start_date'],
                                self._config['LOG_PATH'])
        
        #create an instance of database helper and store it in config dictionary
        self._config["dbhelper"] = SQLiteDatabaseAdapter(self._config)     
        
        #parse config params
        configSuccess = self.checkConfig()
        if(configSuccess == -1):
            return -1
            
        
        #create res dir
        self.createResultsDir()
        
        #calculate estimated time for measurement
#         self.calculateTimeLeft()
            
        #assemble pktgen command
        self.assemblePktgenCommand()
          
        #create symlinks for lua files
        self.createSymlinksForLuaScripts()
        

    
    
    
    
    def checkConfig(self):
        '''
        This function will check the set config parameters and correctness, i.e.,
        whether paths to binaries exist, other config parameters have the right
        type, etc. 
        return - Int: -1 error, 0 otherwise 
        '''
        #check pktgen's directory existence
        if not (os.path.isdir(self._config["PKTGEN_ROOT"])):
            self.log.error("PKTGEN_ROOT (%s) does not exist!" % 
                         self._config["PKTGEN_ROOT"])
            return -1
            
        #ok, pktgen dir exists, check whether the binary exists as well
        pktgen_bin = self._config["PKTGEN_ROOT"] + "/" + \
                     self._config["PKTGEN_BIN"]
        if not (os.path.isfile(pktgen_bin)):
            self.log.error("PKTGEN_BIN (%s) does not exist!" % 
                         pktgen_bin)
            return -1
        
        #check whether nfpa's MAIN_ROOT is set correctly
        if not (os.path.isdir(self._config["MAIN_ROOT"])):
            self.log.error("nfpa's MAIN_ROOT (%s) is not correctly set!" % 
                         self._config["MAIN_ROOT"])
            return -1 
        
        #check PKTGEN port masks and core masks
        #store in temporary variable 'a'
        a = self._config["cpu_port_assign"]
        #var a contains a string like this "2.0,3.1"
        #remove head and tail double quotes
        a = a.replace('"','')
        tmp_max_core_mask = 0
        digits = []
        for i in a.split(','):
           
            #this produces ["2.0","3.1"]
            tmp_i = i.split('.')[0]
            self.log.debug("next desired core num: " + str(tmp_i))
#             for j in (i.split('.',1)[0]):
#                 self.log.warninging(str(j))
                #check whether mutliple core was set (try to convert it to int)
            try:
                int_tmp_i = int(tmp_i)
                #this produces ['"','2','3']
                #put them to digits list
                digits.append(copy.deepcopy(int_tmp_i))
                if int(int_tmp_i) > tmp_max_core_mask:
                    #get the maximum of cpu core masks
                    tmp_max_core_mask = int(int_tmp_i)
            
            ### MULTI CORE HANDLING ###    
            except ValueError as e:
                self.log.info("Multicore coremask has been recognized: %s" % 
                              str(tmp_i))
                self.log.info("parsing...only accepted style: [2-4]")
#                 self.log.debug(str(e))
                #this case is when multiple cores wanted to be used
                #parsing only if core mask is set like [2-4]
                #cut the first and last char, since they are rectangular 
                #parentheses (brackets)
                multi_core = copy.deepcopy(tmp_i[1:(len(tmp_i)-1)])
                #ok, we need to split the string according to the dash between
                #the core numbers 2-4
                min_c = int(multi_core.split('-')[0])
                max_c = int(multi_core.split('-')[1])
                
              
                for mc in range(min_c, max_c+1):
                    #append core nums to digits
                    digits.append(copy.deepcopy(int(mc)))
                    #update max core num if necessary
                    if int(mc) > tmp_max_core_mask:
                        tmp_max_core_mask = int(mc)
#               
                    
#         exit(-1)
        #all right, we got max core mask needs to be used
        #now, check whether the the main cpu_core_mask variable covers it
        #calculate how many bits are required for cpu_core_mask
        #store cpu_core_mask in temporary variable 'b'
        b = self._config["cpu_core_mask"]
        #calculate the required bits
        bin_core_mask = bin(int(b,16))[2:]
        bit_length = len(bin_core_mask)

        #this only checks whether the bitmask is long enough
        if tmp_max_core_mask > bit_length-1:
            #this means that cpu_core_mask is not set correctly, since
            #fewer core are reserved, than is required for assignment
            #define in cpu_port_assign
            self.log.error("Wrong core mask was set!")
            self.log.error("max core id (%d) assigned to ports is the (%dth)"\
                         " bit, however core mask ('%s') only reserves (%d) "\
                         "bits" % (tmp_max_core_mask,
                                   tmp_max_core_mask+1,
                                   b,
                                   bit_length))
            self.log.error("!!! %d > %d !!!" % (tmp_max_core_mask+1,bit_length))
            return -1
        #we need to check the correctness as well, as are the corresponding
        #bits are 1
        bin_core_mask = list(bin_core_mask)

#         self.log.debug(str(bin_core_mask))
        #reverse list for getting the right order -> will be easier to access
        #and check bits via length of the list
        bin_core_mask.reverse()

        self.log.debug("Required CPU ids  :" + str(digits))
        #starts from core num 0 on the left
        self.log.debug("Reserved Core mask:" + str(bin_core_mask) + " (reversed)")

        #check correctness (whether corresponding bit is set in core mask)
        for bit in digits:
            cpu_id = bin_core_mask[int(bit)]
            if(cpu_id != '1'):
                self.log.error("Core mask is not set properly.")
                self.log.error("Required CPU id (%d) is not enabled in core"\
                             " mask!" % int(bit))
                self.log.error("core mask: %s" % str(bin_core_mask))
                self.log.error("Required digits needs to be enabled: %s" %
                             str(digits))
                return -1
        
        self.log.info("CORE MASKS SEEM TO BE CORRECT!")
        
        
        #check port_mask
        pm = self._config["port_mask"]
        print(pm)
        if (pm != '1' and pm != '3'):
            #port mask is mis-configured
            self.log.error("Port mask could be only 1 or 3!")
            return -1
        else:
            if(pm == '1'):
                self.log.debug("PORT MASK IS 1")
#                 self.log.debug("sendPort: %s" % self._config["sendPort"])
#                 self.log.debug("recvPort: %s" % self._config["recvPort"])
                if(self._config["sendPort"] != '0' and 
                   self._config["recvPort"] != '0'):
                    self.log.error("In case of Port mask 1, sendPort and " +\
                                    "recvPort need to be 0!")
                    return -1
 
        #port mask is ok, sendPort and recvPort could be different, for instance,
        #dpdk and/or pktgen is enabled for multiple interfaces, but you only need
        #2 interfaces from them
#         else:
             #port_mask is set correctly, we need to check sendPort and recvPort
#             #accordingly
#             if(pm == 1):
#                 #port mask is 1
#                 if(sendPort != 0 and recvPort != 0):
#                     self.log.error("In case of Port mask 1, sendPort and " +\
#                                    "recvPort need to be 0!")
#                     self.log.error("EXITING...")
#                     exit(-1)
#             else:
#                 #port mask is 3
#                 if(sendPort > 1 and recvPort > 1):
#                     #ports can only be 0 or 1
#                     self.log.error("sendPort and recvPort could only be 0 or 1")
#                     self.log.error("EXITING...")
#                     exit(-1)
#                 else:
#                     #port are in the correct range
#                     if (sendPort == recvPort):
#                         self.log.error("sendPort and recvPort must be " +\
#                                        "different in case of port_mask: %s" % 
#                                        pm)
#                     self.log.error("EXITING...")
#                     exit(-1)
        #PORT MASK = OK
        
        #check biDir param
        try:
            self._config["biDir"] = int((self._config["biDir"]))
            #check the value
            if((self._config["biDir"] != 0) and (self._config["biDir"] != 1)):
                self.log.error("biDir (%s) can only be 1 or 0!" % 
                             self._config["biDir"])
                return -1
        except ValueError as ve:
            self.log.error("biDir (%s) IS NOT A NUMBER!!!" % self._config["biDir"])
            return -1
        
        #check pcap files
        #check config file consistency (traffic types and packet sizes)
        if not self.checkPcapFileExists():
            #there is no pcap file for the given packet size and traffic type
            #or there is no pcap file for realistic traffics
            return -1
            
        warning = False
        #check whether packetsize is set, but no synthetic traffictype is set
        if self._config['packetSizes'] and not self._config["trafficTypes"]:
            self.log.warning("Packetsize(s) set without traffic type(s)")
            self.log.warning("SKIPPING...")
            warning = True
            time.sleep(1)
            
        elif not self._config['packetSizes'] and self._config["trafficTypes"]:
            self.log.warning("Traffic type(s) set without packet size(s)")
            self.log.warning("SKIPPING...")
            warning = True
            time.sleep(1)
        if warning and not self._config['realisticTraffics']:
            self.log.error("Nothing to DO! Check configuration!")
            return -1
            
        self.log.debug("cpu_make: %s" % self._config['cpu_make'])    
        self.log.debug("cpu_model: %s" % self._config['cpu_model'])
        self.log.debug("nic_make: %s" % self._config['nic_make'])
        self.log.debug("nic_model: %s" % self._config['nic_model'])
        self.log.debug("virtualization: %s" % self._config['virtualization'])
        self.log.debug("vnf_name: %s" % self._config['vnf_name'])
        self.log.debug("vnf_driver: %s" % self._config['vnf_driver'])
        self.log.debug("vnf_driver_version: %s" % 
                       self._config['vnf_driver_version'])
        self.log.debug("vnf_version: %s" % self._config['vnf_version'])
        self.log.debug("vnf_function: %s" % self._config['vnf_function'])
        self.log.debug("vnf_comment: %s" % self._config['vnf_comment'])  
        self.log.debug("username: %s" % self._config['username'])
          
#         self._config['password'] = "entertm1"
#         self._config['email'] = "sdn-tmit@sdn.tmit.hu"
#         self.log.debug("password: %s" % self._config['password'])
#         self.log.debug("email: %s" % self._config['email'])
    
         
        
        #checking whether path to database file exists
        self.log.debug("db_path: %s/db/nfpa.db" % 
                       (self._config['MAIN_ROOT']))
        db_file = self._config["MAIN_ROOT"] + "/db/nfpa.db"
        if not (os.path.isfile(db_file)):
            self.log.error("DB_PATH (%s) does not exist!" % 
                         db_file)
            self.log.error("EXITING...")
            exit(-1)    
    
        self._config['dbhelper'].connect()
        #check user
        self._config['dbhelper'].getUser(self._config['username'])
        self._config['dbhelper'].disconnect()
        
        return 0
        
    def createResultsDir(self):
        '''
        This function creates the results dir according to the config
        '''
        #read path from config
        path = self._config["MAIN_ROOT"] + "/" + self._config["RES_DIR"]
        #append a new value to config dictionary
        self._config['RES_PATH'] = path
        
        create_cmd = "mkdir -p " + path
        retval = invoke.invoke(create_cmd)
        if(retval[1] != 0):
            self.log.error("Error during creating results subdir(s)")
            self.log.error("Error: %s" % str(retval[0]))
            self.log.error("Exit_code: %s" % str(retval[1]))
            exit(-1)
        

    def createSymlinksForLuaScripts(self):
        '''
        This function creates symlinks in pktgen's main root directory that
        point to nfpa_simple.lua and nfpa_traffic.lua
        These symlinks are always freshly generated and old one are deleted.
        '''
        #remove all existing nfpa lua scripts
        self.log.info("Remove old symlinks...")
        remove_cmd = "rm -rf " + self._config["PKTGEN_ROOT"] + "/nfpa_simple.lua"  
        retval = invoke.invoke(remove_cmd)
        if(retval[1] != 0):
            self.log.error("Error during removing symlink")
            self.log.error("Error: %s" % str(retval[0]))
            self.log.error("Exit_code: %s" % str(retval[1]))
            exit(-1)
                     

        remove_cmd = "rm -rf " + self._config["PKTGEN_ROOT"] + "/nfpa_traffic.lua"                       
        retval = invoke.invoke(remove_cmd)
        if(retval[1] != 0):
            self.log.error("Error during removing symlink")
            self.log.error("Error: %s" % str(retval[0]))
            self.log.error("Exit_code: %s" % str(retval[1]))
            exit(-1)
        
        
        remove_cmd = "rm -rf " +  self._config["PKTGEN_ROOT"] + \
                     "/nfpa_realistic.lua"                       
        retval = invoke.invoke(remove_cmd)
        if(retval[1] != 0):
            self.log.error("Error during removing symlink")
            self.log.error("Error: %s" % str(retval[0]))
            self.log.error("Exit_code: %s" % str(retval[1]))
            exit(-1)
        
        self.log.info("DONE")
        #create symlink for nfpa_simple.lua
        self.log.info("create symlinks")
        symlink_cmd = "ln -s " + self._config["MAIN_ROOT"] + \
                "/lib/nfpa_simple.lua " + self._config["PKTGEN_ROOT"] + \
                "/nfpa_simple.lua"
        self.log.info(symlink_cmd)  
        retval = invoke.invoke(symlink_cmd)
        if(retval[1] != 0):
            self.log.error("Error during creating symlink")
            self.log.error("Error: %s" % str(retval[0]))
            self.log.error("Exit_code: %s" % str(retval[1]))
            exit(-1)
        
        #create symlink for nfpa_traffic.lua
        self.log.info("create symlinks")
        symlink_cmd = "ln -s " + self._config["MAIN_ROOT"] + \
                "/lib/nfpa_traffic.lua " + self._config["PKTGEN_ROOT"] + \
                "/nfpa_traffic.lua"
        self.log.info(symlink_cmd)  
        retval = invoke.invoke(symlink_cmd)
        if(retval[1] != 0):
            self.log.error("Error during creating symlink")
            self.log.error("Error: %s" % str(retval[0]))
            self.log.error("Exit_code: %s" % str(retval[1]))
            exit(-1)
         
        #create symlink for nfpa_realistic.lua
        self.log.info("create symlinks")
        symlink_cmd = "ln -s " + self._config["MAIN_ROOT"] + \
                "/lib/nfpa_realistic.lua " + self._config["PKTGEN_ROOT"] + \
                "/nfpa_realistic.lua"
        self.log.info(symlink_cmd)  
        retval = invoke.invoke(symlink_cmd)
        if(retval[1] != 0):
            self.log.error("Error during creating symlink")
            self.log.error("Error: %s" % str(retval[0]))
            self.log.error("Exit_code: %s" % str(retval[1]))
            exit(-1)
    
            
    def checkPcapFileExists(self):
        '''
        This functions checks whether a pcap file exists for the desired
        packet size and traffic type
        (called from checkConfig())
        '''
        
        simple_traffic_set = False
        if self._config["trafficTypes"]:
            #only if any traffic type was set
            for traffic_type in self._config["trafficTypes"]:
                #there is no pcap file for simple scenarios, skipping file check
                if traffic_type == "simple":
                    self.log.info("Simple traffic type was set")
                    simple_traffic_set = True
                    continue

                        
                
                else:
                    self.log.info("Checking synthetic traffictype: %s" % traffic_type)
                    for packetSize in self._config["packetSizes"]:               
                        
                        #special traffic type for ul-dl traffic
                        self.log.info("Special bidirectional"
                                          " traffictype: %s ?" % traffic_type)
                        if sbtc.checkSpecialTraffic(traffic_type):
                            self.log.info("### SPECIAL TRAFFICTYPE FOUND - "
                                          "USING DIFFERENT PCAPS FOR DIFFERENT"
                                          "PORTS ###")
                            tmp_tt = sbtc.splitTraffic(traffic_type)
                            #check for the first one
                            pcap1 = self._config["MAIN_ROOT"].strip() 
                            pcap1 += "/PCAP/nfpa." + tmp_tt[0] + "." 
                            pcap1 += packetSize + "bytes.pcap"
                            #check for the second one
                            pcap2 = self._config["MAIN_ROOT"].strip() 
                            pcap2 += "/PCAP/nfpa." + tmp_tt[1] + "." 
                            pcap2 += packetSize + "bytes.pcap"
                            
                            #check pcap file existance for both of them
                            self.log.info("check pcap file existence %s " % 
                                          pcap1)
                            ok1 = os.path.isfile(pcap1)
                            
                            self.log.info("check pcap file existence %s " % 
                                          pcap2)
                            ok2 = os.path.isfile(pcap2)
                            
                            #if any pcap is missing, then nothing can be done
                            #with this setting
                            if ok1 and ok2:
                                ok = True
                            else:
                                ok = False
                                
                        
                        else:
                            self.log.info("-------------------------------- NO")
                            #no special ul-dl traffic type was set
                            pcap = self._config["MAIN_ROOT"].strip()
                            pcap += "/PCAP/nfpa." + traffic_type + "." 
                            pcap +=  packetSize + "bytes.pcap"
                            self.log.info("check pcap file existence %s " % pcap)
                            #if special traffic type was set, check the existence of the
                            #corresponding pcap files
                            ok = os.path.isfile(pcap)
                        
                        if not ok:
                            #PCAP file not found
                            self.log.error("Missing PCAP file for traffic type: %s "
                                         "and packet size: %s not exists" % 
                                         (traffic_type, packetSize))
                            self.log.error("Are you sure you have the corresponding "
                                         "PCAP file(s) in directory: %s/PCAP ?" % 
                                         self._config["MAIN_ROOT"])
#                             #if simple traffic was set, then we could step back
#                             #to only measure simple scenario
#                             if simple_traffic_set:
#                                 self.log.warning("For packet size %s, only simple"
#                                               " traffic could be measured" % 
#                                               packetSize)
#                             #if no simple scenario is required, then nothing can
#                             #be done with a missing pcap file - we need to exit
#                             else:
#                                 self.log.error("No simple traffic type was set. "
#                                               "Unable to handle packetsize %s " % 
#                                               packetSize)
                            return False
                        else:
                            self.log.info("[FOUND]")
             
        
        #check for realistic traffics
        if(self._config["realisticTraffics"]):
            self.log.info("Realistic Traffics was defined...")
            for realistic in self._config["realisticTraffics"]:
                
                ok = False
                #special traffic type for ul-dl traffic
                self.log.info("Checking for special bidirectional"
                                  " traffictype: %s" % realistic)
                if sbtc.checkSpecialTraffic(realistic):
                    self.log.info("### SPECIAL TRAFFICTYPE FOUND - "
                                  "USING DIFFERENT PCAPS FOR DIFFERENT"
                                  "PORTS ###")
                    tmp_tt = sbtc.splitTraffic(realistic)
                    #check for the first one
                    pcap1 = self._config["MAIN_ROOT"].strip() 
                    pcap1 += "/PCAP/nfpa." + tmp_tt[0] + ".pcap" 
                    
                    #check for the second one
                    pcap2 = self._config["MAIN_ROOT"].strip() 
                    pcap2 += "/PCAP/nfpa." + tmp_tt[1] + ".pcap"
                    
                    #check pcap file existance for both of them
                    self.log.info("check pcap file existence %s " % 
                                  pcap1)
                    ok1 = os.path.isfile(pcap1)
                    
                    self.log.info("check pcap file existence %s " % 
                                  pcap2)
                    ok2 = os.path.isfile(pcap2)
                    
                    #if any pcap is missing, then nothing can be done
                    #with this setting
                    if ok1 and ok2:
                        ok = True
                    else:
                        ok = False
                        
                        
                else:
                    #assemble complete path for realistic traffic   
                    pcap = self._config["MAIN_ROOT"] + "/PCAP/nfpa." +\
                                     realistic + ".pcap"
                    self.log.info("Looking for %s" % pcap)
                    ok = os.path.isfile(pcap)
                  
                    
                if not ok:
                    #PCAP file not found
                    self.log.error("Missing PCAP file for traffic type: %s "
                                  % realistic)
                    self.log.error("Are you sure you have the corresponding "
                                 "PCAP file(s) in directory: %s/PCAP ?" % 
                                 self._config["MAIN_ROOT"])
                    return False
                else:
                    self.log.info("[FOUND]")
                    

        #everything is good, pcap files were found for the given 
        #packetsizes and traffic types (including realistic ones if they were 
        #set)
        return True
          
            
    def getConfig(self):
        '''
        This function returns the private variable self._config, which
        holds the parsed configurations as a dictionary
        '''
        return self._config
    
    
    def assemblePktgenCommand(self):
        '''This functions assemble the Pktgen main command from config'''
        pktgen = self._config["PKTGEN_BIN"] 
        pktgen += " -c " +  self._config["cpu_core_mask"]
        pktgen += " -n " +  self._config["mem_channels"]
        pktgen += " -- -T"
        pktgen += " -p " + self._config["port_mask"]
        pktgen += " -P "
        pktgen +=  " -m " + self._config["cpu_port_assign"] 
#         self.log.debug(pktgen)
        return pktgen
        
    
    def generateLuaConfigFile(self, traffic_type, packet_sizes, realistic):
        '''
        This function will create a custom config file for Pktgen LUA script
        Traffic type needs to be set in each call, thus lua script will know
        the scenario and will know under which name the results files need to 
        be saved. In each traffic type this cfg file needs to be freshly created,
        so this function is called for each traffic type
        traffic_type String - e.g. tr2e
        packet_sizes List - in order to indicate somehow to pktgen lua script
        what is it doing at any moment. in simple scenarios, we should set a 
        complete list of packet sizes, however, in special traffic types (pcap
        file with predefined packet size, e.g., PCAP/nfpa.tr2e.64bytes.pcap), 
        we indicate in the config file what is the packetsize it is using
        
        traffic_type string - traffic type
        
        packet_sizes list - desired packet sizes (common list is only used if 
        simple traffic type was set. Otherwise, a list with one element is set 
        for a given traffic type
        
        realistic string - The name of the realistic traffic type 
        '''
        #open config file in pktgen's root directory
        cfg_file_name = self._config["PKTGEN_ROOT"] + "/nfpa.cfg" 
        #open file for writing (it will erased each time 
        lua_cfg_file = open(cfg_file_name, 'w')
        
        #first, print out header info
        lua_cfg_file.write("#THIS FILE IS GENERATED FROM nfpa MAIN APP ")
        lua_cfg_file.write("FOR EACH START OF THE APP!\n")
        lua_cfg_file.write("#DO NOT MODIFY IT MANUALLY....SERIOUSLY!\n")
        lua_cfg_file.write("#ON THE OTHER HAND, IT WON'T TAKE ANY EFFECT!\n")
        lua_cfg_file.write("#THESE ARE CONFIGURATIONS FOR PKTGEN LUA SCRIPT!\n")
        lua_cfg_file.write("\n\n")

        #write out measurement duration
        lua_cfg_file.write("measurementDuration=" + 
                           self._config["measurementDuration"])
        lua_cfg_file.write("\n\n")
        
        #write out sending port
        lua_cfg_file.write("sendPort=" + self._config["sendPort"])
        lua_cfg_file.write("\n\n")
        #write out receiving port
        lua_cfg_file.write("recvPort=" + self._config["recvPort"])
        lua_cfg_file.write("\n\n")
        
        #write out bi-direction request
        lua_cfg_file.write("biDir=" + str(self._config["biDir"]))
        lua_cfg_file.write("\n\n")
        
        
        #write out packetSizes if set
        if packet_sizes is not None:
            for i in packet_sizes:
                lua_cfg_file.write("packetSize=" + i)
                lua_cfg_file.write("\n")
            lua_cfg_file.write("\n\n")

# 
        #write out trafficTypes if set
        if traffic_type is not None:
            lua_cfg_file.write("trafficType=" + traffic_type)
            lua_cfg_file.write("\n\n")


        #write out realistic traffic name
        #if not set, do not write out. 
        if realistic is not None:
            lua_cfg_file.write("realisticTraffic=" + realistic)
            lua_cfg_file.write("\n\n")

        
        lua_cfg_file.write("\n")
     
        #close file
        lua_cfg_file.close()

        

        
        
        
        
        
        
        
        
        
                