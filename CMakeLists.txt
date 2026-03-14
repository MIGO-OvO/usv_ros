cmake_minimum_required(VERSION 3.0.2)
project(usv_ros)

## Find catkin macros and libraries
find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  std_srvs
  geometry_msgs
  mavros_msgs
)

###################################
## catkin specific configuration ##
###################################
catkin_package(
  CATKIN_DEPENDS
    rospy
    std_msgs
    std_srvs
    geometry_msgs
    mavros_msgs
)

#############
## Install ##
#############

## Mark executable scripts (Python etc.) for installation
catkin_install_python(PROGRAMS
  scripts/pump_control_node.py
  scripts/spectrometer_node.py
  scripts/mission_coordinator_node.py
  scripts/web_config_server.py
  scripts/mavlink_trigger_node.py
  scripts/usv_mavlink_bridge.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

## Mark launch files for installation
install(DIRECTORY launch/
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/launch
  FILES_MATCHING PATTERN "*.launch"
)

## Mark config files for installation
install(DIRECTORY config/
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/config
  FILES_MATCHING PATTERN "*.yaml"
)

install(DIRECTORY static/
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/static
)
