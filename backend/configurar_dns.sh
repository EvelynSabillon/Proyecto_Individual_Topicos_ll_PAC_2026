#!/bin/bash
ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o LogLevel=ERROR -o StrictHostKeyChecking=accept-new -i '/root/.ssh/mikrotik_tea_key' 'admin@192.168.56.10' 'ip dns set servers=8.8.8.8,8.8.4.4 allow-remote-requests=yes'
