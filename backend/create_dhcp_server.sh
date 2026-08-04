#!/bin/bash

ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o LogLevel=ERROR -o StrictHostKeyChecking=accept-new -i '/root/.ssh/mikrotik_tea_key' 'admin@192.168.56.10' 'ip dhcp-server add name=dhcp_ether2 interface=ether2 address-pool=pool_ether2 disabled=no'
