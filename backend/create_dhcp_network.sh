#!/bin/bash

ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o LogLevel=ERROR -o StrictHostKeyChecking=accept-new -i '/root/.ssh/mikrotik_tea_key' 'admin@192.168.56.10' 'ip dhcp-server network add address=192.168.70.0/24 gateway=192.168.70.1 dns-server=8.8.8.8,8.8.4.4'
