#!/bin/bash
# ---------------------------------------------------------------------
# Generado automaticamente por mikrotik_system.py.
# NO editar a mano: se sobrescribe en cada ejecucion y queda en disco
# como evidencia de lo que se le mando al router.
# ---------------------------------------------------------------------
ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o LogLevel=ERROR -o StrictHostKeyChecking=accept-new -i '/home/evelyn/.ssh/mikrotik_tea_key' 'admin@192.168.56.10' 'ip dhcp-server remove [find name=dhcp_lan1]'
