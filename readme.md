# Bluetooth "Wall of Sheep"

A little app that discovers bluetooth devices near by.

## Features

 - The "Wall of Sheep".  A list of all bluetooth devices and useful information.
 - Writes a current device list to a csv file.
 - Identifies the company of a device using a public listing.
 - Calculates distance from transmitter and receiver of a device with TX Power and RSSI.
 - Removes devices that have not been present for certain amount of time. This combats the "private resolvable random addresses" feature that prevents tracking of devices.
 - Highlights devices that tend to "stick around"

## Roadmap
 
 - Configurable columns
 - Resolve services by name
 - Estimates distance from transmitter and receiver of a device given ONLY RSSI if know distance values are present.
 - Load spinner on first load. It's boring to see nothing in a table
 - Fingerprint devices that keep changing MAC addresses
 - Show adv data
 - Go into service data
 - Resolve common service->characteristics such as temp/humidity
 - attempt to keep same indexes of current devices

## Build Portable

`pyinstaller index.py -F -n bluetooth-wos`


## Interesting problems
  - "private resolvable random addresses" happens with a lot of devices! Can we indicate which devices?

## References
 - https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/uuids/service_uuids.yaml