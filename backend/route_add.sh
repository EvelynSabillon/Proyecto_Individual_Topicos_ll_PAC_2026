#!/bin/bash

ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o LogLevel=ERROR -o StrictHostKeyChecking=accept-new -i '/root/.ssh/mikrotik_tea_key' 'admin@192.168.56.10' 'ip route add dst-address=192.168.90.0/24 gateway=192.168.56.2 comment="Ruta hacia sucursal"'
