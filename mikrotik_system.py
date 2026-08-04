"""
===============================================================================
 CONTROL MIKROTIK ROUTER
 Administracion de un router MikroTik (RouterOS) desde Python sobre Linux
===============================================================================

 ARQUITECTURA  (Frontend + Backend en un solo archivo, separados por capas)
 -------------------------------------------------------------------------

   BACKEND   (secciones 0 a 6)
        reciben datos, escriben un script
       .sh con esos datos, lo ejecutan y devuelven (ok, titulo, detalle).

   FRONTEND  (secciones 7 a 9)
       le pide al backend que lo haga y muestra el resultado.

 COMO VIAJA UNA ORDEN HASTA EL ROUTER
 ------------------------------------
       FRONTEND (CustomTkinter)
            |   el usuario llena el formulario y pulsa el boton
            v
       BACKEND / operacion op_*()      valida -> ejecuta -> VERIFICA
            |
            v
       ejecutar("create_IP.sh", "ip address add ...")
            |   escribe el .sh en backend/ con los parametros dentro
            v
       bash backend/create_IP.sh
            |
            v
       ssh -i <llave> <usuario>@<ip> 'comando de RouterOS'
            |
            v
       RouterOS  ->  la respuesta se CAPTURA y se interpreta

 Los scripts .sh son el backend de shell scripting se regeneran en cada ejecucion con los 
 parametros que puso el usuario y quedan en disco como evidencia auditable de lo 
 que se le mando al router.

 SOBRE PERMISOS Y VELOCIDAD
 --------------------------
 Aqui NO se usa sudo en ningun momento, ni chmod 777, ni archivos .txt
 intermedios:
   - Los .sh se escriben en la carpeta backend/ que esta al lado de este
     archivo, o sea que pertenece al mismo usuario que corre el programa.
     Se les pone permiso de ejecucion con os.chmod(0o755) desde Python.
   - La salida del router se captura directo de la tuberia del proceso
     (subprocess), sin pasar por "> archivo.txt" y despues "cat archivo.txt".
   - Toda llamada a la red lleva timeout, asi que la aplicacion nunca se
     queda colgada esperando a un router apagado.
   - Las operaciones corren en un hilo aparte, asi que la ventana nunca se
     congela mientras el router contesta.
===============================================================================
"""

import ipaddress
import os
import re
import subprocess
import threading
import time
from datetime import datetime


# =============================================================================
#  SECCION 0 - CONFIGURACION Y RUTAS
# =============================================================================

# --- Datos del router (valores por defecto) ----------------------------------
IP = "192.168.56.10"                                        # IP del MikroTik
USUARIO = "admin"                                           # usuario de RouterOS
LLAVE = os.path.expanduser("~/.ssh/mikrotik_tea_key")       # llave privada SSH
TIMEOUT = 5                                                 # segundos de espera

# --- Interfaces que se monitorean por defecto --------------------------------
INTERFAZ_1 = "ether1"
INTERFAZ_2 = "ether2"

# --- Rutas del proyecto (se calculan solas) ----------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")     # scripts .sh generados
RUNTIME_DIR = os.path.join(BASE_DIR, "runtime")     # archivos del monitoreo
BACKUPS_DIR = os.path.join(BASE_DIR, "Backups")     # respaldos traidos del router
IMAGENES_DIR = os.path.join(BASE_DIR, "imagenes")   # iconos on.png / off.png
CONEXION_INI = os.path.join(BASE_DIR, "conexion.ini")

for _carpeta in (BACKEND_DIR, RUNTIME_DIR, BACKUPS_DIR, IMAGENES_DIR):
    if not os.path.isdir(_carpeta):
        os.makedirs(_carpeta)

# --- Archivos que escriben los scripts de monitoreo en segundo plano ---------
F_ESTADO_ICMP = os.path.join(RUNTIME_DIR, "estado.txt")
F_DATOS_PING = os.path.join(RUNTIME_DIR, "datosconexion.txt")
F_DATOS_INTERFACES = os.path.join(RUNTIME_DIR, "datosinterfaces.txt")
F_ESTADO_1 = os.path.join(RUNTIME_DIR, "estado1.txt")
F_ESTADO_2 = os.path.join(RUNTIME_DIR, "estado2.txt")
F_TRAFICO_1 = os.path.join(RUNTIME_DIR, "trafico1.txt")
F_TRAFICO_2 = os.path.join(RUNTIME_DIR, "trafico2.txt")

# Registro historico del trafico
F_LOG_TRAFICO = os.path.join(RUNTIME_DIR, "trafico.log")
MAX_LINEAS_LOG = 500        # se recorta al pasar de aqui

# --- Scripts de monitoreo (se escriben solos al arrancar) --------------------
SH_VERIFICAR_CONEXION = os.path.join(BACKEND_DIR, "verificarconexion.sh")
SH_MONITOREAR_IP = os.path.join(BACKEND_DIR, "monitorearip.sh")
SH_VERIFICAR_INTERFACES = os.path.join(BACKEND_DIR, "verificarinterfaces.sh")
SH_MONITOREAR_INTERFACES = os.path.join(BACKEND_DIR, "monitorearinterfaces.sh")

# Evita que dos hilos escriban el mismo .sh a la vez.
_CANDADO_SH = threading.Lock()


def cargar_conexion():
    """Lee conexion.ini y sustituye los valores por defecto, si existe.

    Formato, una clave por linea:
        ip = 192.168.56.10
        usuario = admin
        llave = ~/.ssh/mikrotik_tea_key

    Se lee a mano, sin configparser, para que sea simple y editable con
    cualquier editor de texto.
    """
    global IP, USUARIO, LLAVE, INTERFAZ_1, INTERFAZ_2

    if not os.path.isfile(CONEXION_INI):
        return

    try:
        with open(CONEXION_INI, "r", encoding="utf-8") as pf:
            for linea in pf:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, valor = linea.split("=", 1)
                clave, valor = clave.strip().lower(), valor.strip()
                if not valor:
                    continue
                if clave == "ip":
                    IP = valor
                elif clave == "usuario":
                    USUARIO = valor
                elif clave == "llave":
                    LLAVE = os.path.expanduser(valor)
                elif clave == "interfaz1":
                    INTERFAZ_1 = valor
                elif clave == "interfaz2":
                    INTERFAZ_2 = valor
    except OSError:
        # Si el archivo esta corrupto se sigue con los valores por defecto
        # en vez de impedir que la aplicacion arranque.
        pass


def guardar_conexion_ini():
    """Escribe conexion.ini con los valores que hay ahora en memoria."""
    texto = (
        "# Datos de conexion con el router MikroTik.\n"
        "# Lo escribe la pagina 'Conexion SSH' de la aplicacion, pero se\n"
        "# puede editar a mano con cualquier editor de texto.\n"
        "ip = " + IP + "\n"
        "usuario = " + USUARIO + "\n"
        "llave = " + LLAVE + "\n"
        "interfaz1 = " + INTERFAZ_1 + "\n"
        "interfaz2 = " + INTERFAZ_2 + "\n"
    )
    try:
        with open(CONEXION_INI, "w", encoding="utf-8") as pf:
            pf.write(texto)
        return True, ""
    except OSError as e:
        return False, str(e)


cargar_conexion()


# =============================================================================
#  SECCION 1 - BACKEND - INTERPRETACION DE LAS RESPUESTAS DEL ROUTER
# =============================================================================
#
#  Se distinguen tres situaciones porque al usuario le sirve un mensaje
#  distinto en cada una:
#     1. No hay router           -> "revisa que este encendido"
#     2. Hay router pero dijo no -> se le muestra literalmente su respuesta
#     3. Ya estaba hecho         -> no es un fallo: el estado buscado ya esta
# =============================================================================

# Marcas que aparecen cuando el router RECHAZA un comando
ERRORES = (
    "failure", "error", "expected", "no such item", "invalid",
    "cannot", "input does not match", "bad command", "ambiguous",
    "syntax error",
)

# Marcas de que el problema no es el comando, sino que el router NO RESPONDE
CONEXION = (
    "connection timed out", "no route to host", "connection refused",
    "could not resolve", "host key verification", "connection closed",
    "permission denied", "operation timed out", "network is unreachable",
    "broken pipe", "no such file or directory", "identity file",
    "tiempo de espera agotado",
)

# Respuestas que significan "ya estaba hecho", no "salio mal"
YA_EXISTE = ("already exists", "such name exists", "already have", "already has")


def es_error_conexion(salida):
    """True si la salida indica que no se pudo llegar al router."""
    if not salida:
        return False
    bajo = salida.lower()
    return any(marca in bajo for marca in CONEXION)


def hubo_fallo(salida):
    """True si el router rechazo el comando o no se pudo llegar a el."""
    if not salida:
        return False
    bajo = salida.lower()
    if any(marca in bajo for marca in YA_EXISTE):
        return True
    return (any(marca in bajo for marca in ERRORES)
            or any(marca in bajo for marca in CONEXION))


def ya_existia(salida):
    """True si el router dijo que la cosa ya existia."""
    if not salida:
        return False
    bajo = salida.lower()
    return any(marca in bajo for marca in YA_EXISTE)


# =============================================================================
#  SECCION 2 - BACKEND - VALIDACIONES
# =============================================================================
#  Se valida ANTES de mandar nada al router. Es mas rapido y el mensaje de
#  error es mucho mas claro que el que devuelve RouterOS.
# =============================================================================

def limpiar(valor):
    """Quita espacios sobrantes y convierte None en cadena vacia."""
    return str(valor).strip() if valor is not None else ""


def _octetos_ok(partes):
    """True si las cuatro partes de una IP son numeros de 0 a 255."""
    if len(partes) != 4:
        return False
    for parte in partes:
        if not parte.isdigit() or len(parte) > 3:
            return False
        if not 0 <= int(parte) <= 255:
            return False
    return True


def validar_ip(valor):
    """Valida una IP suelta: 192.168.56.10"""
    valor = limpiar(valor)
    if not valor:
        return False, "El campo esta vacio."
    if not _octetos_ok(valor.split(".")):
        return False, ("'" + valor + "' no es una IP valida.\n"
                       "Formato esperado: 192.168.56.10")
    return True, ""


def validar_cidr(valor):
    """Valida una direccion con mascara: 192.168.56.10/24"""
    valor = limpiar(valor)
    if not valor:
        return False, "El campo esta vacio."
    if "/" not in valor:
        return False, ("A '" + valor + "' le falta la mascara.\n"
                       "Formato esperado: 192.168.56.10/24")
    ip, mascara = valor.split("/", 1)
    if not _octetos_ok(ip.split(".")):
        return False, ("'" + ip + "' no es una IP valida.\n"
                       "Formato esperado: 192.168.56.10/24")
    if not mascara.isdigit() or not 0 <= int(mascara) <= 32:
        return False, "La mascara debe ser un numero entre 0 y 32."
    return True, ""


def red_normalizada(destino):
    """Direccion de red que RouterOS realmente guarda para un destino.

    RouterOS descarta los bits de host: si se escribe 192.168.56.20/24 la
    ruta queda guardada como 192.168.56.0/24. Sin normalizar aqui, el
    chequeo posterior a la creacion comparaba el texto tal cual lo
    escribio el usuario contra lo que de verdad hay en el router y
    siempre reportaba fallo aunque la ruta se hubiera creado bien.
    """
    try:
        return str(ipaddress.ip_network(destino, strict=False))
    except ValueError:
        return destino


def validar_rango(valor):
    """Valida un rango de pool: 192.168.56.50-192.168.56.254"""
    valor = limpiar(valor)
    if not valor:
        return False, "El campo esta vacio."
    if "-" not in valor:
        return False, ("Al rango le falta el guion.\n"
                       "Formato esperado: 192.168.56.50-192.168.56.254")
    inicio, fin = valor.split("-", 1)
    for extremo, etiqueta in ((inicio, "inicial"), (fin, "final")):
        ok, msg = validar_ip(extremo)
        if not ok:
            return False, "La IP " + etiqueta + " del rango no es valida. " + msg
    # Que el final no sea menor que el inicio
    n_ini = [int(x) for x in limpiar(inicio).split(".")]
    n_fin = [int(x) for x in limpiar(fin).split(".")]
    if n_fin < n_ini:
        return False, "La IP final del rango es menor que la inicial."
    return True, ""


def validar_nombre(valor, que="nombre"):
    """Valida un nombre para RouterOS: letras, numeros, guiones y puntos."""
    valor = limpiar(valor)
    if not valor:
        return False, "El " + que + " esta vacio."
    if len(valor) > 40:
        return False, "El " + que + " no puede pasar de 40 caracteres."
    if not re.match(r"^[A-Za-z0-9._-]+$", valor):
        return False, ("El " + que + " solo admite letras, numeros, punto,\n"
                       "guion y guion bajo (sin espacios ni acentos).")
    return True, ""


def validar_interfaz(valor):
    """Valida el nombre de una interfaz."""
    valor = limpiar(valor)
    if not valor:
        return False, "No se ha seleccionado ninguna interfaz."
    if not re.match(r"^[A-Za-z0-9._/-]+$", valor):
        return False, "'" + valor + "' no es un nombre de interfaz valido."
    return True, ""


def validar_comentario(valor):
    """El comentario es opcional, pero no puede llevar comillas dobles."""
    valor = limpiar(valor)
    if not valor:
        return True, ""
    if len(valor) > 80:
        return False, "El comentario no puede pasar de 80 caracteres."
    if '"' in valor:
        return False, "El comentario no puede llevar comillas dobles."
    return True, ""


def validar_lista_dns(valor):
    """Valida una lista de servidores DNS separados por coma."""
    valor = limpiar(valor)
    if not valor:
        return False, "No se indico ningun servidor DNS."
    for servidor in valor.split(","):
        ok, msg = validar_ip(servidor)
        if not ok:
            return False, "En la lista de DNS: " + msg
    return True, ""


def escapar(valor):
    """Escapa un texto para meterlo entre comillas simples en el shell.

    El comando viaja asi:  ssh ... 'ip address add comment="loquesea"'
    Se usa el truco estandar de bash: cerrar, escapar y volver a abrir:
        '  ->  '\\''
    """
    return str(valor).replace("'", "'\\''")


# =============================================================================
#  SECCION 3 - BACKEND - COMUNICACION CON EL ROUTER
# =============================================================================
#  Todo lo que sale hacia el router pasa por una de estas dos funciones:
#
#      ejecutar(archivo_sh, comando)  -> para CAMBIAR algo en el router
#      consultar(comando)             -> para PREGUNTAR algo al router
#
#  Las dos escriben un script .sh con los parametros ya dentro y lo lanzan
#  con bash, capturando la respuesta. Sin sudo, sin chmod 777 y sin archivos
#  .txt intermedios.
# =============================================================================

def linea_ssh(comando_router):
    """Arma la linea ssh completa para un comando de RouterOS.

    Opciones usadas:
      -T                     sin pseudo-terminal: la salida sale limpia
      -o BatchMode=yes       si la llave falla, corta en vez de pedir
                             contrasena y colgar la ventana
      -o ConnectTimeout=N    no espera indefinidamente a un router apagado
      -o LogLevel=ERROR      quita el ruido de "added to known hosts"
      -o StrictHostKeyChecking=accept-new
                             acepta la huella la primera vez, pero avisa si
                             cambia despues
    """
    return (
        "ssh -T"
        " -o BatchMode=yes"
        " -o ConnectTimeout=" + str(TIMEOUT) +
        " -o LogLevel=ERROR"
        " -o StrictHostKeyChecking=accept-new"
        " -i '" + escapar(LLAVE) + "'"
        " '" + escapar(USUARIO + "@" + IP) + "'"
        " '" + escapar(comando_router) + "'"
    )


CABECERA_SH = (
    "#!/bin/bash\n"
    "# ---------------------------------------------------------------------\n"
    "# Generado automaticamente por mikrotik_system.py.\n"
    "# NO editar a mano: se sobrescribe en cada ejecucion y queda en disco\n"
    "# como evidencia de lo que se le mando al router.\n"
    "# ---------------------------------------------------------------------\n"
)


def _escribir_y_correr(archivo_sh, comando_router, timeout_extra=20):
    """Escribe el .sh con el comando dentro, lo ejecuta y captura la salida.

    Devuelve (codigo_de_salida, texto). codigo -1 significa que no se pudo
    lanzar el script.
    """
    ruta = os.path.join(BACKEND_DIR, archivo_sh)
    contenido = CABECERA_SH + linea_ssh(comando_router) + "\n"

    with _CANDADO_SH:
        try:
            with open(ruta, "w", encoding="utf-8") as pf:
                pf.write(contenido)
            # Permiso de ejecucion; sin sudo, backend/ es del mismo usuario.
            os.chmod(ruta, 0o755)
        except OSError as e:
            return -1, "No se pudo escribir el script " + archivo_sh + ": " + str(e)

        try:
            proc = subprocess.run(["bash", ruta],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT,
                                  timeout=TIMEOUT + timeout_extra)
        except subprocess.TimeoutExpired:
            return -1, ("Tiempo de espera agotado: el router no contesto en "
                        + str(TIMEOUT + timeout_extra) + " segundos.")
        except OSError as e:
            return -1, "No se pudo ejecutar bash: " + str(e)

    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


def ejecutar(archivo_sh, comando_router):
    """Aplica un cambio en el router. Devuelve (ok, salida).

    ok es True solo si bash termino bien Y el router no se quejo.
    """
    codigo, salida = _escribir_y_correr(archivo_sh, comando_router)
    ok = (codigo == 0) and not hubo_fallo(salida)
    return ok, salida


def consultar(comando_router, archivo_sh="consulta.sh"):
    """Pregunta algo al router y devuelve su respuesta como texto.

    NUNCA lanza excepcion: si el ssh falla devuelve el texto del error, para
    que un router apagado no reviente la aplicacion.
    """
    _, salida = _escribir_y_correr(archivo_sh, comando_router, timeout_extra=10)
    return salida


def correr_pasos(pasos):
    """Ejecuta una lista de pasos y devuelve (todo_ok, reporte).

    Cada paso es una tupla:  (titulo, archivo_sh, comando [, tolerar])
    NO se detiene en el primer fallo: sigue y reporta el estado de cada uno.

    Si `tolerar` es True y el router dice que la cosa ya existe, el paso se
    marca YA EXISTIA y no cuenta como fallo.
    """
    lineas = []
    todo_ok = True

    for paso in pasos:
        titulo, archivo, comando = paso[0], paso[1], paso[2]
        tolerar = paso[3] if len(paso) > 3 else False

        ok, salida = ejecutar(archivo, comando)

        if ok:
            estado = "OK"
        elif tolerar and ya_existia(salida):
            estado = "YA EXISTIA"
        else:
            estado = "FALLO"
            todo_ok = False

        lineas.append(titulo.ljust(32, ".") + " " + estado)
        if estado == "FALLO" and salida:
            lineas.append("      el router dijo: " + salida.replace("\n", " "))

    return todo_ok, "\n".join(lineas)


def hay_conexion():
    """Devuelve (ok, detalle). Se llama antes de cada accion.

    Asi un router apagado produce un mensaje claro en vez de un fallo raro a
    mitad de una secuencia de comandos.
    """
    prueba = consultar("system identity print", "probar_conexion.sh")
    if not prueba or es_error_conexion(prueba):
        return False, (prueba or "el router no respondio nada")
    return True, prueba


def find_addr(valor):
    """Devuelve el [find ...] que de verdad encuentra una direccion.

    En RouterOS  [find address=192.168.56.0/24]  NO encuentra nada: address
    es de tipo prefijo, no texto, y la comparacion directa falla en silencio.
    Hay que convertir el campo a texto con :tostr antes de comparar.
    """
    return '[find where [:tostr $address]="' + valor + '"]'


# =============================================================================
#  SECCION 4 - BACKEND - CONSULTAS AL ROUTER
# =============================================================================
#  Las funciones get_* devuelven listas de Python y sirven para llenar los
#  desplegables del frontend. Las print_* devuelven el texto tal cual lo
#  imprime RouterOS y se muestran en el panel de resultado.
# =============================================================================

def get_interfaces():
    """Lista TODAS las interfaces del router, tengan IP o no.

    Si solo se listaran las que ya tienen IP, seria imposible ponerle la
    primera direccion a una interfaz libre desde el desplegable.
    """
    salida = consultar(":foreach i in=[/interface find] do={"
                       ":put [/interface get $i name]}", "consulta.sh")
    if hubo_fallo(salida):
        return []
    return [x for x in salida.split() if x]


def get_ips():
    """Lista las direcciones IP como texto 'x.x.x.x/nn'."""
    salida = consultar(":foreach i in=[/ip address find] do={"
                       ":put [:tostr [/ip address get $i address]]}",
                       "consulta.sh")
    if hubo_fallo(salida):
        return []
    return [x for x in salida.split() if x]


def get_ips_con_interfaz():
    """Lista textos 'direccion  (interfaz)' en UNA sola consulta.

    Se piden juntos y no en dos consultas separadas porque, si una interfaz
    tiene dos IPs, emparejarlos despues por posicion sale mal.
    """
    salida = consultar(
        ":foreach i in=[/ip address find] do={"
        ':put ([:tostr [/ip address get $i address]] . "|" . '
        '[:tostr [/ip address get $i interface]])}', "consulta.sh")
    if hubo_fallo(salida):
        return []

    resultado = []
    for linea in salida.splitlines():
        linea = linea.strip()
        if "|" in linea:
            direccion, interfaz = linea.split("|", 1)
            resultado.append(direccion.strip() + "   (" + interfaz.strip() + ")")
        elif linea:
            resultado.append(linea)
    return resultado


def solo_direccion(texto):
    """De 'x.x.x.x/nn   (ether2)' devuelve solo 'x.x.x.x/nn'."""
    return limpiar(texto).split()[0] if limpiar(texto) else ""


def get_dhcp_servers():
    """Nombres de los servidores DHCP configurados."""
    salida = consultar(":foreach i in=[/ip dhcp-server find] do={"
                       ":put [/ip dhcp-server get $i name]}", "consulta.sh")
    if hubo_fallo(salida):
        return []
    return [x for x in salida.split() if x]


def get_pools():
    """Nombres de los pools de direcciones."""
    salida = consultar(":foreach i in=[/ip pool find] do={"
                       ":put [/ip pool get $i name]}", "consulta.sh")
    if hubo_fallo(salida):
        return []
    return [x for x in salida.split() if x]


def get_redes_dhcp():
    """Redes DHCP como texto 'x.x.x.x/nn'."""
    salida = consultar(":foreach i in=[/ip dhcp-server network find] do={"
                       ":put [:tostr [/ip dhcp-server network get $i address]]}",
                       "consulta.sh")
    if hubo_fallo(salida):
        return []
    return [x for x in salida.split() if x]


def get_rutas_estaticas():
    """Destinos de las rutas estaticas (las que puso el administrador).

    Se filtra por static=yes para no ofrecer borrar las rutas que el propio
    router genera solas para sus interfaces.
    """
    salida = consultar(":foreach i in=[/ip route find static=yes] do={"
                       ":put [:tostr [/ip route get $i dst-address]]}",
                       "consulta.sh")
    if hubo_fallo(salida):
        return []
    return [x for x in salida.split() if x]


def get_rutas_estaticas_pares():
    """(destino, gateway) de cada ruta estatica.

    Hace falta el par completo -y no solo el destino- porque puede haber
    varias rutas estaticas hacia la misma red con gateways distintos.
    """
    salida = consultar(
        ":foreach i in=[/ip route find static=yes] do={"
        ':put ([:tostr [/ip route get $i dst-address]] . "|" . '
        '[:tostr [/ip route get $i gateway]])}', "consulta.sh")
    if hubo_fallo(salida):
        return []
    pares = []
    for linea in salida.split():
        if "|" in linea:
            destino, gateway = linea.split("|", 1)
            pares.append((destino, gateway))
    return pares


def get_rutas_detalle():
    """Texto con destino, gateway y comentario de cada ruta estatica."""
    salida = consultar(
        ":foreach i in=[/ip route find static=yes] do={"
        ':put ([:tostr [/ip route get $i dst-address]] . "  via  " . '
        '[:tostr [/ip route get $i gateway]] . "   " . '
        '[:tostr [/ip route get $i comment]])}', "consulta.sh")
    if hubo_fallo(salida):
        return ""
    return salida


def get_dns_router():
    """Servidores DNS configurados en el router, como texto."""
    salida = consultar(":put [:tostr [/ip dns get servers]]", "consulta.sh")
    if hubo_fallo(salida):
        return ""
    return limpiar(salida)


def lista_dns(texto):
    """Convierte '8.8.8.8,8.8.4.4' en un conjunto, para poder comparar.

    Se usan conjuntos y no cadenas porque el router puede devolver los
    servidores en otro orden del que se le pidieron.
    """
    if not texto:
        return set()
    return set(x.strip() for x in limpiar(texto).split(",") if x.strip())


# --- Comprobaciones de existencia --------------------------------------------

def existe_ip(direccion):
    """True si el router tiene esa direccion IP."""
    return limpiar(direccion) in get_ips()


def existe_red_dhcp(red):
    """True si el router tiene esa red DHCP."""
    return limpiar(red) in get_redes_dhcp()


def existe_ruta_estatica(destino):
    """True si el router tiene una ruta estatica hacia ese destino."""
    return limpiar(destino) in get_rutas_estaticas()


def existe_ruta_estatica_par(destino, gateway):
    """True si el router tiene una ruta estatica exacta destino/gateway."""
    return (limpiar(destino), limpiar(gateway)) in get_rutas_estaticas_pares()


def dhcp_server_de_interfaz(interfaz):
    """Nombre del servidor DHCP que ya ocupa esa interfaz, o cadena vacia.

    RouterOS solo admite un servidor DHCP por interfaz, asi que conviene
    avisarlo antes de crear el segundo.
    """
    salida = consultar(
        ':foreach i in=[/ip dhcp-server find interface="' + limpiar(interfaz) +
        '"] do={:put [/ip dhcp-server get $i name]}', "consulta.sh")
    if hubo_fallo(salida):
        return ""
    partes = [x for x in salida.split() if x]
    return partes[0] if partes else ""


def dhcp_server_invalido(nombre):
    """Devuelve 'true', 'false', 'no existe' o el texto del error.

    RouterOS marca como invalid a un servidor DHCP que creo pero no puede
    usar (por ejemplo, porque la interfaz no tiene IP).
    """
    salida = consultar(
        ':local n [/ip dhcp-server find name="' + limpiar(nombre) + '"];'
        ':if ([:len $n] > 0) do={:put [:tostr [/ip dhcp-server get $n invalid]]}'
        ' else={:put "no existe"}', "consulta.sh")
    salida = limpiar(salida)
    if hubo_fallo(salida) and salida not in ("true", "false", "no existe"):
        return salida
    return salida


# --- Consultas en crudo para el panel de resultado ---------------------------

def print_identity():
    return consultar("system identity print", "consulta.sh")


def print_ips():
    return consultar("ip address print", "consulta.sh")


def print_dns():
    return consultar("ip dns print", "consulta.sh")


def print_rutas():
    return consultar("ip route print", "consulta.sh")


def print_dhcp_servers():
    return consultar("ip dhcp-server print", "consulta.sh")


def print_dhcp_networks():
    return consultar("ip dhcp-server network print", "consulta.sh")


def print_pools():
    return consultar("ip pool print", "consulta.sh")


def print_leases():
    return consultar("ip dhcp-server lease print", "consulta.sh")


def print_interfaces():
    return consultar("interface print", "consulta.sh")


def print_backups_router():
    """Lista los archivos .backup que hay dentro del router."""
    salida = consultar(
        ':foreach f in=[/file find] do={'
        ':local n [/file get $f name];'
        ':if ([:find $n ".backup"] >= 0) do={'
        ':put ($n . "   " . [:tostr [/file get $f size]] . " bytes")}}',
        "consulta.sh")
    if hubo_fallo(salida) or not salida:
        return "(el router no reporta respaldos)"
    return salida


# =============================================================================
#  SECCION 5 - BACKEND - OPERACIONES DE ADMINISTRACION
# =============================================================================
#  Cada funcion de esta seccion sigue siempre los mismos cinco pasos:
#     1. valida lo que escribio el usuario
#     2. comprueba que el router este vivo
#     3. ejecuta el comando (o la secuencia de comandos) via .sh
#     4. VERIFICA preguntandole al router que el cambio quedara aplicado
#     5. devuelve (ok, titulo, detalle) para que la pagina lo muestre
# =============================================================================

def _sin_router():
    """Devuelve la terna de error si el router no responde, o None si si."""
    ok, detalle = hay_conexion()
    if ok:
        return None
    return (False, "Sin conexion con el router",
            "No se pudo contactar a " + IP + ".\n\n"
            "Detalle:\n" + detalle + "\n\n"
            "Revisa que:\n"
            "  - el router este encendido\n"
            "  - la red interna de VirtualBox este bien conectada\n"
            "  - la llave " + LLAVE + " exista y este importada en RouterOS\n\n"
            "La pagina 'Conexion SSH' permite probar y arreglar esto.")


def _fallo(titulo, salida):
    """Convierte una salida de error en (ok, titulo, detalle).

    Distingue 'el router dijo que no' de 'no hay router'.
    """
    if es_error_conexion(salida):
        return (False, "Sin conexion con el router",
                "Se perdio la conexion con " + IP + " a mitad de la operacion.\n\n"
                "Detalle:\n" + salida)
    return (False, titulo, "El router respondio:\n" + (salida or "(sin respuesta)"))


# ------------------------------- 1. IDENTIDAD --------------------------------

def op_set_nombre(nombre):
    """Asigna el nombre (identity) del router."""
    ok, msg = validar_nombre(nombre, "nombre del router")
    if not ok:
        return False, "Nombre invalido", msg

    sin = _sin_router()
    if sin:
        return sin

    nombre = limpiar(nombre)
    ok, salida = ejecutar("routername.sh", "system identity set name=" + nombre)
    if not ok:
        return _fallo("No se pudo asignar el nombre", salida)

    # Verificacion: se lo preguntamos al router en vez de suponer
    actual = limpiar(consultar(":put [/system identity get name]", "consulta.sh"))
    if actual != nombre:
        return (False, "El nombre no quedo aplicado",
                "Se pidio '" + nombre + "' pero el router reporta '" + actual + "'.")

    return (True, "Nombre asignado",
            "El router se llama ahora: " + nombre + "\n\n" + print_identity())


# ---------------------------- 2 y 3. DIRECCIONES IP --------------------------

def op_crear_ip(direccion, interfaz, comentario):
    """Agrega una direccion IP a una interfaz."""
    ok, msg = validar_cidr(direccion)
    if not ok:
        return False, "Direccion invalida", msg
    ok, msg = validar_interfaz(interfaz)
    if not ok:
        return False, "Interfaz invalida", msg
    ok, msg = validar_comentario(comentario)
    if not ok:
        return False, "Comentario invalido", msg

    sin = _sin_router()
    if sin:
        return sin

    direccion, interfaz = limpiar(direccion), limpiar(interfaz)
    comentario = limpiar(comentario)

    if existe_ip(direccion):
        return (False, "Esa IP ya existe",
                "El router ya tiene la direccion " + direccion + ".\n\n" + print_ips())

    # Sin esto se mandaria comment= vacio y el router lo rechaza
    cmd = "ip address add address=" + direccion + " interface=" + interfaz
    if comentario:
        cmd += ' comment="' + comentario + '"'

    ok, salida = ejecutar("create_IP.sh", cmd)
    if not ok:
        return _fallo("No se pudo crear la IP", salida)

    if not existe_ip(direccion):
        return (False, "La IP no aparece en el router",
                "El comando no dio error pero " + direccion +
                " no esta en la lista.\n\n" + print_ips())

    return (True, "Direccion IP creada",
            "IP " + direccion + " en la interfaz " + interfaz + "\n\n" + print_ips())


def op_eliminar_ip(direccion):
    """Elimina una IP buscandola POR DIRECCION, no por numero de fila.

    Buscar por fila era lo que hacia que, con dos IPs en la misma interfaz,
    se borrara la direccion equivocada.
    """
    direccion = solo_direccion(direccion)
    ok, msg = validar_cidr(direccion)
    if not ok:
        return False, "Direccion invalida", msg

    sin = _sin_router()
    if sin:
        return sin

    if not existe_ip(direccion):
        return (False, "No existe esa IP",
                "El router no tiene la direccion " + direccion + ".\n\n" + print_ips())

    ok, salida = ejecutar("IPDelete.sh",
                          "ip address remove " + find_addr(direccion))
    if not ok:
        return _fallo("No se pudo eliminar la IP", salida)

    # Un remove sobre un find vacio no da error: sin esto un dedazo se veria
    # igual que un exito.
    if existe_ip(direccion):
        return (False, "La IP sigue en el router",
                "El comando no dio error pero " + direccion +
                " todavia aparece.\n\n" + print_ips())

    return (True, "Direccion IP eliminada",
            "Se elimino " + direccion + "\n\n" + print_ips())


# --------------------------- 4 y 5. SERVIDOR DHCP ----------------------------

def op_crear_dhcp(interfaz, ip_interfaz, pool, rango, servidor, red, gateway, dns):
    """Crea un servidor DHCP completo en cuatro pasos y verifica el resultado.

    Orden necesario:
        0. IP en la interfaz  -> sin ella el servidor nace INVALID
        1. Pool de direcciones
        2. Servidor DHCP apuntando al pool
        3. Red con gateway y DNS
    """
    for valor, validador, etiqueta in (
            (interfaz, validar_interfaz, "Interfaz"),
            (ip_interfaz, validar_cidr, "IP de la interfaz"),
            (rango, validar_rango, "Rango del pool"),
            (red, validar_cidr, "Red"),
            (gateway, validar_ip, "Gateway")):
        ok, msg = validador(valor)
        if not ok:
            return False, "Dato invalido", etiqueta + ": " + msg

    for valor, etiqueta in ((pool, "nombre del pool"),
                            (servidor, "nombre del servidor")):
        ok, msg = validar_nombre(valor, etiqueta)
        if not ok:
            return False, "Dato invalido", msg

    dns = limpiar(dns)
    if dns:
        ok, msg = validar_lista_dns(dns)
        if not ok:
            return False, "DNS invalido", msg

    sin = _sin_router()
    if sin:
        return sin

    interfaz, ip_interfaz = limpiar(interfaz), limpiar(ip_interfaz)
    pool, rango = limpiar(pool), limpiar(rango)
    servidor, red, gateway = limpiar(servidor), limpiar(red), limpiar(gateway)

    # RouterOS solo admite un servidor DHCP por interfaz
    ocupado = dhcp_server_de_interfaz(interfaz)
    if ocupado and ocupado != servidor:
        return (False, "La interfaz ya tiene servidor DHCP",
                interfaz + " ya esta ocupada por el servidor '" + ocupado + "'.\n\n"
                "RouterOS no admite dos servidores DHCP en el mismo puerto.\n"
                "Elimina el anterior desde ELIMINAR DHCP y vuelve a intentarlo.")

    cmd_red = "ip dhcp-server network add address=" + red + " gateway=" + gateway
    if dns:
        cmd_red += " dns-server=" + dns

    # tolerar=True: que el pool o la IP ya existan no es un fallo.
    pasos = [
        ("0) IP en " + interfaz, "create_ip_interface.sh",
         "ip address add address=" + ip_interfaz + " interface=" + interfaz, True),
        ("1) Pool " + pool, "create_dhcp_pool.sh",
         "ip pool add name=" + pool + " ranges=" + rango, True),
        ("2) Servidor " + servidor, "create_dhcp_server.sh",
         "ip dhcp-server add name=" + servidor + " interface=" + interfaz +
         " address-pool=" + pool + " disabled=no", True),
        ("3) Red y DNS", "create_dhcp_network.sh", cmd_red, True),
    ]

    todo_ok, reporte = correr_pasos(pasos)

    # Verificacion real: le preguntamos al router si el servidor quedo activo
    estado = dhcp_server_invalido(servidor)

    if estado == "false":
        veredicto = "SERVIDOR ACTIVO: el router lo acepta y ya reparte IPs."
    elif estado == "true":
        veredicto = ("SERVIDOR INVALIDO: el router lo creo pero no lo usa.\n"
                     "Causa tipica: la interfaz " + interfaz + " no tiene IP,\n"
                     "o el gateway " + gateway + " no pertenece a la red " + red + ".")
        todo_ok = False
    elif estado == "no existe":
        veredicto = ("EL SERVIDOR '" + servidor + "' NO EXISTE: no se llego a crear.\n"
                     "Mira arriba cual de los pasos fallo.")
        todo_ok = False
    else:
        veredicto = "NO SE PUDO VERIFICAR:\n" + str(estado)
        todo_ok = False

    titulo = "Servidor DHCP creado" if todo_ok else "Servidor DHCP con problemas"
    return (todo_ok, titulo,
            reporte + "\n\nVerificacion en el router:\n" + veredicto +
            "\n\nServidores DHCP:\n" + print_dhcp_servers())


def op_eliminar_dhcp(servidor, pool, red):
    """Elimina servidor, pool y red DHCP, en ese orden.

    El orden importa: el pool no se puede borrar mientras un servidor lo use.
    Borrar solo el servidor deja huerfanos el pool y la red.
    """
    ok, msg = validar_nombre(servidor, "nombre del servidor")
    if not ok:
        return False, "Dato invalido", msg

    pool, red = limpiar(pool), limpiar(red)
    if pool:
        ok, msg = validar_nombre(pool, "nombre del pool")
        if not ok:
            return False, "Dato invalido", msg
    if red:
        ok, msg = validar_cidr(red)
        if not ok:
            return False, "Red invalida", msg

    sin = _sin_router()
    if sin:
        return sin

    servidor = limpiar(servidor)

    pasos = [("1) Servidor " + servidor, "delete_dhcp_server.sh",
              "ip dhcp-server remove [find name=" + servidor + "]")]
    if pool:
        pasos.append(("2) Pool " + pool, "delete_dhcp_pool.sh",
                      "ip pool remove [find name=" + pool + "]"))
    if red:
        pasos.append(("3) Red " + red, "delete_dhcp_network.sh",
                      "ip dhcp-server network remove " + find_addr(red)))

    todo_ok, reporte = correr_pasos(pasos)

    # Comprobar que de verdad desaparecieron
    avisos = []
    if servidor in get_dhcp_servers():
        avisos.append("El servidor " + servidor + " sigue en el router.")
        todo_ok = False
    if red and existe_red_dhcp(red):
        avisos.append("La red " + red + " sigue en el router.")
        todo_ok = False

    detalle = reporte
    if avisos:
        detalle += "\n\nATENCION:\n" + "\n".join("  - " + a for a in avisos)
    detalle += ("\n\nServidores DHCP:\n" + print_dhcp_servers() +
                "\nRedes DHCP:\n" + print_dhcp_networks())

    return (todo_ok,
            "DHCP eliminado" if todo_ok else "DHCP eliminado con fallos",
            detalle)


# ------------------------------- 6 y 7. DNS ----------------------------------

def op_configurar_dns(servidores, permitir_remoto):
    """Configura los servidores DNS del router."""
    ok, msg = validar_lista_dns(servidores)
    if not ok:
        return False, "DNS invalido", msg

    sin = _sin_router()
    if sin:
        return sin

    servidores = limpiar(servidores)
    remoto = "yes" if permitir_remoto else "no"

    ok, salida = ejecutar("configurar_dns.sh",
                          "ip dns set servers=" + servidores +
                          " allow-remote-requests=" + remoto)
    if not ok:
        return _fallo("No se pudo configurar el DNS", salida)

    # Verificacion DESPUES de aplicar, comparando conjuntos porque el router
    # puede devolver los servidores en otro orden.
    actual = get_dns_router()
    if lista_dns(servidores) != lista_dns(actual):
        return (False, "El DNS no quedo como se pidio",
                "Se pidieron: " + servidores + "\n"
                "El router reporta: " + (actual or "(vacio)") + "\n\n" + print_dns())

    return (True, "Servidor DNS configurado",
            "Servidores: " + servidores + "\n"
            "Peticiones remotas: " + remoto + "\n\n" + print_dns())


def op_eliminar_dns():
    """Deja el router sin servidores DNS y sin peticiones remotas."""
    sin = _sin_router()
    if sin:
        return sin

    ok, salida = ejecutar("eliminar_dns.sh",
                          'ip dns set servers="" allow-remote-requests=no')
    if not ok:
        return _fallo("No se pudo eliminar la configuracion DNS", salida)

    actual = get_dns_router()
    if actual:
        return (False, "El DNS sigue configurado",
                "El router todavia reporta: " + actual + "\n\n" + print_dns())

    return (True, "Configuracion DNS eliminada",
            "El router quedo sin servidores DNS.\n\n" + print_dns())


# -------------------------- 8 y 9. RUTAS ESTATICAS ---------------------------

def op_crear_ruta(destino, gateway, comentario):
    """Crea una ruta estatica."""
    ok, msg = validar_cidr(destino)
    if not ok:
        return False, "Destino invalido", msg
    ok, msg = validar_ip(gateway)
    if not ok:
        return False, "Gateway invalido", msg
    ok, msg = validar_comentario(comentario)
    if not ok:
        return False, "Comentario invalido", msg

    sin = _sin_router()
    if sin:
        return sin

    destino, gateway = limpiar(destino), limpiar(gateway)
    comentario = limpiar(comentario)
    destino = red_normalizada(destino)

    cmd = "ip route add dst-address=" + destino + " gateway=" + gateway
    if comentario:
        cmd += ' comment="' + comentario + '"'

    ok, salida = ejecutar("route_add.sh", cmd)
    if not ok:
        return _fallo("No se pudo crear la ruta", salida)

    if not existe_ruta_estatica(destino):
        return (False, "La ruta no aparece en el router",
                "El comando no dio error pero la ruta hacia " + destino +
                " no esta en la lista.\n\n" + print_rutas())

    return (True, "Ruta estatica creada",
            destino + " via " + gateway + "\n\nRutas estaticas ahora:\n" +
            (get_rutas_detalle() or "(ninguna)"))


def op_eliminar_ruta(destino, gateway):
    """Elimina una ruta estatica. No toca las que genera el propio router.

    Se exige el gateway ademas del destino porque puede haber varias rutas
    estaticas hacia la misma red con gateways distintos (ver captura del
    caso real: dos rutas a 192.168.56.0/24), y borrar solo por destino
    borraria todas a la vez.
    """
    ok, msg = validar_cidr(destino)
    if not ok:
        return False, "Destino invalido", msg
    ok, msg = validar_ip(gateway)
    if not ok:
        return False, "Gateway invalido", msg

    sin = _sin_router()
    if sin:
        return sin

    destino, gateway = limpiar(destino), limpiar(gateway)

    if not existe_ruta_estatica_par(destino, gateway):
        return (False, "No existe esa ruta estatica",
                "El router no tiene una ruta estatica hacia " + destino +
                " via " + gateway + ".\n\n"
                "Rutas estaticas actuales:\n" + (get_rutas_detalle() or "(ninguna)"))

    # static=yes protege las rutas que el router crea solo para sus interfaces
    ok, salida = ejecutar("route_remove.sh",
                          "ip route remove [find dst-address=" + destino +
                          " gateway=" + gateway + " static=yes]")
    if not ok:
        return _fallo("No se pudo eliminar la ruta", salida)

    if existe_ruta_estatica_par(destino, gateway):
        return (False, "La ruta sigue en el router",
                "El comando no dio error pero " + destino + " via " + gateway +
                " todavia aparece.")

    return (True, "Ruta estatica eliminada",
            "Se elimino " + destino + " via " + gateway + "\n\nRutas estaticas ahora:\n" +
            (get_rutas_detalle() or "(ninguna)"))


# --------------------------- 12 y 13. RESPALDOS ------------------------------

def op_crear_respaldo():
    """Crea el respaldo en el router y lo trae al PC, comprobando cada paso.

    No se usa un sleep fijo: se espera a que el archivo aparezca de verdad en
    el router, y despues se comprueba que llegue al PC y no pese 0 bytes.
    """
    sin = _sin_router()
    if sin:
        return sin

    nombre = "backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    archivo = nombre + ".backup"
    destino = os.path.join(BACKUPS_DIR, archivo)

    # 1) Pedirle al router que lo guarde
    ok, salida = ejecutar("respaldoMK.sh", "system backup save name=" + nombre)
    if not ok:
        return _fallo("No se pudo crear el respaldo", salida)

    # 2) Esperar a que aparezca. El router tarda un momento en escribirlo y
    #    ese tiempo no es constante, asi que se pregunta hasta 10 veces.
    aparecio = False
    for _ in range(10):
        r = consultar(':put [:tostr [/file find name="' + archivo + '"]]',
                      "consulta.sh")
        if r and not hubo_fallo(r):
            aparecio = True
            break
        time.sleep(1)

    if not aparecio:
        return (False, "El respaldo no aparecio en el router",
                "Se pidio guardar " + archivo + " pero el router no lo muestra\n"
                "despues de 10 segundos.\n\nRespaldos en el router:\n" +
                print_backups_router())

    # 3) Traerlo al PC con scp
    try:
        proc = subprocess.run(
            ["scp", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=" + str(TIMEOUT),
             "-o", "StrictHostKeyChecking=accept-new",
             "-i", LLAVE,
             USUARIO + "@" + IP + ":" + archivo, destino],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=TIMEOUT + 40)
        salida_scp = proc.stdout.decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        return False, "scp tardo demasiado", "La copia del respaldo no termino a tiempo."
    except OSError as e:
        return False, "No se pudo ejecutar scp", str(e)

    # 4) Comprobar que llego y que no esta vacio
    if proc.returncode != 0 or not os.path.exists(destino):
        return (False, "El respaldo quedo en el router pero no se copio",
                "scp respondio:\n" + (salida_scp or "(sin respuesta)") +
                "\n\nEl archivo " + archivo + " si existe en el router.")

    tam = os.path.getsize(destino)
    if tam == 0:
        os.remove(destino)
        return (False, "El respaldo llego vacio",
                "Se copio " + archivo + " pero pesaba 0 bytes, asi que se descarto.")

    return (True, "Respaldo creado",
            "Archivo:  " + archivo + "\n"
            "Guardado: " + destino + "\n"
            "Tamano:   " + str(round(tam / 1024.0, 1)) + " KiB\n\n" +
            listar_respaldos_texto())


def listar_respaldos():
    """Lista los .backup guardados en el PC, del mas nuevo al mas viejo."""
    try:
        archivos = [f for f in os.listdir(BACKUPS_DIR) if f.endswith(".backup")]
    except OSError:
        return []
    archivos.sort(reverse=True)
    return archivos


def listar_respaldos_texto():
    """Texto con nombre, tamano y fecha de cada respaldo guardado en el PC."""
    archivos = listar_respaldos()
    if not archivos:
        return ("Respaldos en este equipo:\n"
                "(todavia no hay ninguno)\n\nCarpeta: " + BACKUPS_DIR)

    lineas = ["Respaldos en este equipo:", "Carpeta: " + BACKUPS_DIR, ""]
    for f in archivos:
        ruta = os.path.join(BACKUPS_DIR, f)
        try:
            tam = os.path.getsize(ruta)
            fecha = datetime.fromtimestamp(os.path.getmtime(ruta))
            lineas.append(f.ljust(34) +
                          (str(round(tam / 1024.0, 1)) + " KiB").rjust(11) +
                          "   " + fecha.strftime("%d/%m/%Y %H:%M"))
        except OSError:
            lineas.append(f + "   (no se pudo leer)")
    return "\n".join(lineas)


def op_eliminar_respaldo(nombre):
    """Borra un respaldo del PC. No toca los del router."""
    nombre = limpiar(nombre)
    if not nombre:
        return False, "Sin seleccion", "No se ha seleccionado ningun respaldo."

    # Nunca construir una ruta sin comprobarla: un nombre con ../ podria
    # borrar cualquier archivo del sistema.
    if os.path.sep in nombre or nombre.startswith("."):
        return False, "Nombre invalido", "Nombre de respaldo no permitido."

    ruta = os.path.join(BACKUPS_DIR, nombre)
    if not os.path.isfile(ruta):
        return (False, "No existe ese respaldo",
                "No se encontro " + nombre + " en " + BACKUPS_DIR)

    try:
        os.remove(ruta)
    except OSError as e:
        return False, "No se pudo eliminar", str(e)

    return (True, "Respaldo eliminado",
            "Se elimino " + nombre + "\n\n" + listar_respaldos_texto())


# =============================================================================
#  SECCION 5B - BACKEND - AUTENTICACION POR LLAVE SSH
# =============================================================================
#  Sin esta seccion, antes de poder usar la aplicacion habria que hacer tres
#  cosas a mano en la terminal:
#
#     1. ssh-keygen -t rsa -b 2048 -f ~/.ssh/mikrotik_tea_key
#     2. scp ~/.ssh/mikrotik_tea_key.pub admin@<ip>:/
#     3. (dentro del router)
#        /user ssh-keys import public-key-file=mikrotik_tea_key.pub user=admin
#
#  Aqui se hacen desde la propia ventana.
# =============================================================================

import pty
import select
import fcntl
import termios


def _hacerse_terminal_de_control():
    """Convierte el pty (ya en fd 0 tras el dup2 de Popen) en terminal de
    control del proceso.

    Sin esto, start_new_session=True desengancha al hijo de cualquier
    terminal pero el dup2 no vuelve a asignarle uno: ssh/scp intentan abrir
    /dev/tty para pedir la contrasena, no lo encuentran y caen a
    SSH_ASKPASS, que casi nunca esta instalado. El sintoma es
    "Permission denied, please try again" en bucle aunque la contrasena
    sea correcta.
    """
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def llave_publica():
    """Ruta de la llave publica, deducida de la privada."""
    return LLAVE + ".pub"


def _hay_que_contestar(texto):
    """True si lo que ha escrito el proceso es una peticion de contrasena."""
    return re.search(r"password|contrase|passphrase", texto, re.IGNORECASE) is not None


def dialogo_con_password(argv, password, timeout=60):
    """Ejecuta un comando dandole la contrasena por un terminal fabricado.

    Devuelve (codigo_de_salida, texto_de_la_conversacion). El proceso cree
    tener un terminal de verdad delante, la unica forma de que ssh y scp
    acepten una contrasena sin intervencion humana.
    """
    maestro, esclavo = pty.openpty()

    try:
        proceso = subprocess.Popen(argv, stdin=esclavo, stdout=esclavo,
                                   stderr=esclavo, close_fds=True,
                                   start_new_session=True,
                                   preexec_fn=_hacerse_terminal_de_control)
    except OSError as e:
        os.close(maestro)
        os.close(esclavo)
        return 127, "No se pudo lanzar " + argv[0] + ": " + str(e)

    # El esclavo ya es del proceso hijo; el padre debe soltarlo o nunca vera
    # el fin de la lectura cuando el hijo termine.
    os.close(esclavo)

    trozos = []
    contestado = False
    limite = time.time() + timeout

    while True:
        if time.time() > limite:
            proceso.kill()
            trozos.append("\n(tiempo de espera agotado)")
            break

        listos, _, _ = select.select([maestro], [], [], 0.3)

        if listos:
            try:
                datos = os.read(maestro, 4096)
            except OSError:
                break                      # el hijo cerro su lado del terminal
            if not datos:
                break
            texto = datos.decode("utf-8", "replace")
            trozos.append(texto)

            if not contestado and _hay_que_contestar("".join(trozos)):
                os.write(maestro, (password + "\n").encode())
                contestado = True

        elif proceso.poll() is not None:
            break                          # termino y ya no queda nada por leer

    os.close(maestro)

    try:
        proceso.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proceso.kill()

    conversacion = "".join(trozos)

    # Por si ssh llegara a hacer eco de lo tecleado: la contrasena no debe
    # acabar en el panel de resultado ni en un pantallazo.
    if password:
        conversacion = conversacion.replace(password, "********")

    return (proceso.returncode if proceso.returncode is not None else 1,
            conversacion.strip())


def _opciones_password():
    """Opciones de ssh/scp para forzar autenticacion por contrasena.

    Hacen falta porque, si ya existe una llave a medio instalar, ssh la
    intentaria primero y la sesion moriria antes de pedir la contrasena.
    """
    return ["-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=" + str(TIMEOUT),
            "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=password,keyboard-interactive"]


# --------------------------- Datos de la conexion ----------------------------

def op_guardar_conexion(ip, usuario, llave):
    """Guarda IP, usuario y ruta de la llave en conexion.ini."""
    global IP, USUARIO, LLAVE

    ok, msg = validar_ip(ip)
    if not ok:
        return False, "IP invalida", msg
    ok, msg = validar_nombre(usuario, "usuario")
    if not ok:
        return False, "Usuario invalido", msg

    llave = os.path.expanduser(limpiar(llave))
    if not llave:
        return False, "Llave invalida", "La ruta de la llave privada esta vacia."
    if not os.path.isabs(llave):
        return (False, "Llave invalida",
                "Escribe la ruta completa, por ejemplo:\n"
                "  ~/.ssh/mikrotik_tea_key")

    IP, USUARIO, LLAVE = limpiar(ip), limpiar(usuario), llave

    ok, err = guardar_conexion_ini()
    if not ok:
        return False, "No se pudo guardar", err

    aviso = ""
    if not os.path.isfile(LLAVE):
        aviso = ("\n\nAVISO: todavia no existe el archivo\n  " + LLAVE +
                 "\nUsa el paso 1 para crearlo.")

    return (True, "Conexion guardada",
            "Router : " + USUARIO + "@" + IP + "\n"
            "Llave  : " + LLAVE + "\n"
            "Archivo: " + CONEXION_INI + aviso)


def op_probar_conexion():
    """Prueba la conexion por llave y devuelve el detalle."""
    ok, detalle = hay_conexion()
    if not ok:
        return (False, "Sin conexion",
                "No se pudo entrar a " + USUARIO + "@" + IP + " con la llave.\n\n"
                "Detalle:\n" + detalle + "\n\n" + estado_llaves_texto())
    return (True, "Conexion correcta",
            "El router contesto sin pedir contrasena.\n\n" + detalle + "\n\n"
            "Interfaces:\n" + (print_interfaces() or "(sin datos)"))


# ------------------------------ Estado actual --------------------------------

def permisos_llave():
    """Permisos actuales de la llave privada, en octal (por ejemplo "600").

    Devuelve cadena vacia si el archivo no existe o no se puede leer.
    """
    try:
        return oct(os.stat(LLAVE).st_mode & 0o777)[2:]
    except OSError:
        return ""


def estado_llaves_texto():
    """Resumen legible de las llaves y de si la autenticacion funciona."""
    privada = os.path.isfile(LLAVE)
    publica = os.path.isfile(llave_publica())

    permisos = permisos_llave() or "?"

    if privada:
        autentica, detalle = hay_conexion()
    else:
        autentica, detalle = False, "Todavia no hay llave privada."

    lineas = [
        "PASO 1  Llave privada",
        "        " + LLAVE,
        "        " + (("EXISTE, permisos " + permisos)
                      if privada else "NO EXISTE"),
        "",
        "        Llave publica",
        "        " + llave_publica(),
        "        " + ("EXISTE" if publica else "NO EXISTE"),
        "",
        "PASOS 2 y 3  Instalacion en el router",
        "        Destino       : " + USUARIO + "@" + IP,
        "        Autenticacion : " + ("FUNCIONA, ya no pide contrasena"
                                      if autentica else "TODAVIA NO FUNCIONA"),
    ]

    if not autentica:
        lineas += ["", "        Detalle:"]
        lineas += ["        " + x for x in detalle.splitlines()[:6]]

    if privada and permisos not in ("600", "400"):
        lineas += ["",
                   "AVISO: la llave privada tiene permisos " + permisos + ".",
                   "ssh se niega a usar una llave que puedan leer otros",
                   "usuarios de la maquina. Pulsa 'Arreglar permisos'."]

    return "\n".join(lineas)


def texto_llave_publica():
    """Contenido de la llave publica mas las instrucciones para hacerlo a mano.

    Es la salida de emergencia si la instalacion automatica falla: deja a
    mano lo que hay que copiar y los dos comandos.
    """
    ruta = llave_publica()
    if not os.path.isfile(ruta):
        return ("Todavia no existe " + ruta + ".\n"
                "Genera primero el par de llaves con el paso 1.")
    try:
        with open(ruta, "r", encoding="utf-8") as pf:
            contenido = pf.read().strip()
    except OSError as e:
        return "No se pudo leer " + ruta + ": " + str(e)

    return (
        "Contenido de " + ruta + ":\n\n" + contenido + "\n\n"
        "-------------------------------------------------------------\n"
        "Si prefieres hacerlo a mano, son estos dos comandos:\n\n"
        "  En el PC:\n"
        "    scp " + ruta + " " + USUARIO + "@" + IP + ":/\n\n"
        "  Dentro del router (consola de RouterOS):\n"
        "    /user ssh-keys import public-key-file=" +
        os.path.basename(ruta) + " user=" + USUARIO + "\n")


def listar_llaves_router():
    """Llaves que el router tiene registradas para este usuario."""
    salida = consultar("user ssh-keys print", "consulta.sh")
    if not salida or hubo_fallo(salida):
        return "No se pudo leer la lista de llaves del router.\n\n" + (salida or "")
    return salida


# ------------------------- Paso 1: crear el par ------------------------------

def op_generar_llaves(bits=2048, sobrescribir=False):
    """Crea el par de llaves con ssh-keygen.

    Se usa RSA y no ed25519 porque las versiones antiguas de RouterOS solo
    aceptan RSA al importar.

    La llave se crea SIN passphrase (-N ""): con passphrase habria que
    teclearla en cada comando que manda la aplicacion.
    """
    if bits not in (2048, 4096):
        return False, "Tamano invalido", "Elige 2048 o 4096 bits."

    if os.path.isfile(LLAVE) and not sobrescribir:
        return (False, "La llave ya existe",
                "Ya hay una llave privada en:\n  " + LLAVE + "\n\n"
                "Si la sustituyes, el router dejara de reconocerte hasta que\n"
                "instales la nueva llave publica (pasos 2 y 3).\n\n"
                "Marca 'Sobrescribir' si de verdad quieres reemplazarla.")

    carpeta = os.path.dirname(LLAVE)
    try:
        if carpeta and not os.path.isdir(carpeta):
            os.makedirs(carpeta, 0o700)
    except OSError as e:
        return False, "No se pudo crear la carpeta", str(e)

    # ssh-keygen se niega a sobrescribir sin preguntar, asi que los archivos
    # anteriores se quitan antes de llamarlo.
    for archivo in (LLAVE, llave_publica()):
        if os.path.isfile(archivo):
            try:
                os.remove(archivo)
            except OSError as e:
                return False, "No se pudo borrar la llave anterior", str(e)

    argv = ["ssh-keygen", "-t", "rsa", "-b", str(bits), "-f", LLAVE,
            "-N", "", "-C", "mikrotik-" + USUARIO + "@" + IP]

    try:
        proceso = subprocess.run(argv, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=120)
        salida = proceso.stdout.decode("utf-8", "replace").strip()
    except FileNotFoundError:
        return (False, "No se encontro ssh-keygen",
                "Instalalo con:\n  sudo apt install openssh-client")
    except OSError as e:
        # PermissionError y demas fallos al lanzar el proceso. Sin esto la
        # excepcion subia hasta el hilo trabajador como "Error inesperado".
        return (False, "No se pudo ejecutar ssh-keygen",
                str(e) + "\n\nComprueba que ssh-keygen exista y tenga permiso\n"
                "de ejecucion:  ls -l $(which ssh-keygen)")
    except subprocess.TimeoutExpired:
        return False, "ssh-keygen tardo demasiado", "No termino en 2 minutos."

    if proceso.returncode != 0 or not os.path.isfile(LLAVE):
        return False, "No se pudo generar la llave", salida or "(sin respuesta)"

    try:
        os.chmod(LLAVE, 0o600)
    except OSError:
        pass

    return (True, "Par de llaves creado",
            "Privada : " + LLAVE + "  (permisos 600)\n"
            "Publica : " + llave_publica() + "\n"
            "Tipo    : RSA de " + str(bits) + " bits, sin passphrase\n\n"
            "Ahora hay que instalarla en el router: pasos 2 y 3.\n\n" + salida)


# ------------------- Paso 2: subir la llave publica --------------------------

def op_subir_llave(password):
    """Copia el archivo .pub a la raiz del sistema de archivos del router."""
    if not limpiar(password):
        return (False, "Falta la contrasena",
                "Escribe la contrasena del usuario '" + USUARIO + "' del router.\n"
                "Solo hace falta para este paso y el siguiente: se usa en\n"
                "memoria y no se guarda en ningun sitio.")

    pub = llave_publica()
    if not os.path.isfile(pub):
        return (False, "No existe la llave publica",
                "No se encontro:\n  " + pub + "\n\n"
                "Haz primero el paso 1.")

    argv = ["scp"] + _opciones_password() + [pub, USUARIO + "@" + IP + ":/"]
    codigo, conversacion = dialogo_con_password(argv, password)

    if codigo != 0:
        return (False, "No se pudo subir la llave publica",
                "scp termino con codigo " + str(codigo) + ".\n\n"
                "Conversacion con el router:\n" +
                (conversacion or "(sin respuesta)") + "\n\n"
                "Causas habituales:\n"
                "  - la contrasena no es la correcta\n"
                "  - el router no responde en " + IP + "\n"
                "  - el servicio ssh esta desactivado en /ip service")

    # Comprobacion real: scp puede devolver 0 sin haber dejado nada util.
    nombre = os.path.basename(pub)
    listado = consultar(':put [:tostr [/file find name="' + nombre + '"]]',
                        "consulta.sh")
    aviso = ""
    if not listado or hubo_fallo(listado):
        aviso = ("\n\nNota: no se pudo confirmar el archivo en el router porque\n"
                 "la llave todavia no autentica. Es normal en este paso.")

    return (True, "Llave publica subida",
            "Se copio " + nombre + " a la raiz del router.\n\n" +
            (conversacion or "(scp no dijo nada, que es lo normal)") + aviso +
            "\n\nSiguiente: paso 3, importarla dentro de RouterOS.")


# ----------------- Paso 3: importarla dentro de RouterOS ---------------------

def op_importar_llave(password):
    """Ejecuta /user ssh-keys import dentro del router."""
    if not limpiar(password):
        return (False, "Falta la contrasena",
                "Escribe la contrasena del usuario '" + USUARIO + "' del router.")

    nombre = os.path.basename(llave_publica())
    comando = ("/user ssh-keys import public-key-file=" + nombre +
               " user=" + USUARIO)

    argv = ["ssh", "-T"] + _opciones_password() + [USUARIO + "@" + IP, comando]
    codigo, conversacion = dialogo_con_password(argv, password)

    if codigo != 0 or hubo_fallo(conversacion):
        return (False, "No se pudo importar la llave",
                "El router respondio:\n" + (conversacion or "(sin respuesta)") +
                "\n\nSi dice que no encuentra el archivo, repite el paso 2.")

    # La prueba definitiva no es lo que conteste el import, sino si ya se
    # puede entrar SIN contrasena.
    ok, detalle = hay_conexion()
    if not ok:
        return (False, "Se importo, pero aun no autentica",
                "El router acepto el import, pero entrar con la llave sigue\n"
                "fallando.\n\nDetalle:\n" + detalle + "\n\n"
                "Comprueba que la llave privada sea " + LLAVE + " y que tenga\n"
                "permisos 600.")

    return (True, "Autenticacion por llave lista",
            "El router ya reconoce la llave y no volvera a pedir contrasena.\n\n"
            "Llaves registradas en el router:\n" + listar_llaves_router() +
            "\n\n" + estado_llaves_texto())


# --------------------- Los tres pasos, uno detras de otro --------------------

def op_instalar_llave_completa(password, bits=2048, sobrescribir=False):
    """Hace los pasos 1, 2 y 3 seguidos y devuelve un parte de cada uno.

    Informa exactamente en que paso se quedo si algo falla.
    """
    partes = []

    # Paso 1: si ya existe una llave utilizable, no se toca.
    if os.path.isfile(LLAVE) and not sobrescribir:
        partes.append("1) Crear par de llaves ...... YA EXISTIA")
    else:
        ok, titulo_texto, detalle = op_generar_llaves(bits, sobrescribir)
        if not ok:
            partes.append("1) Crear par de llaves ...... FALLO")
            return False, titulo_texto, "\n".join(partes) + "\n\n" + detalle
        partes.append("1) Crear par de llaves ...... OK")

    ok, titulo_texto, detalle = op_subir_llave(password)
    partes.append("2) Subir la llave publica ... " + ("OK" if ok else "FALLO"))
    if not ok:
        return False, titulo_texto, "\n".join(partes) + "\n\n" + detalle

    ok, titulo_texto, detalle = op_importar_llave(password)
    partes.append("3) Importar en RouterOS ..... " + ("OK" if ok else "FALLO"))

    return (ok,
            "Llave instalada" if ok else "Se quedo en el paso 3",
            "\n".join(partes) + "\n\n" + detalle)


# ------------------------------- Mantenimiento -------------------------------

def op_arreglar_permisos():
    """Deja la llave privada en 600, que es lo que exige ssh."""
    if not os.path.isfile(LLAVE):
        return False, "No existe la llave", "No se encontro:\n  " + LLAVE
    try:
        os.chmod(LLAVE, 0o600)
    except OSError as e:
        return False, "No se pudieron cambiar los permisos", str(e)
    return (True, "Permisos corregidos",
            "La llave privada quedo con permisos 600.\n\n" + estado_llaves_texto())


# =============================================================================
#  SECCION 6 - BACKEND - MONITOREO EN SEGUNDO PLANO
# =============================================================================
#  El monitoreo lo hacen cuatro scripts de bash que corren
#  en segundo plano, en pareja productor -> consumidor.
#
#      verificarinterfaces.sh   ---->   monitorearinterfaces.sh
#        pregunta al router               interpreta y escribe
#        y escribe datosinterfaces.txt    estado1/2.txt y trafico1/2.txt
#
#      verificarconexion.sh     ---->     monitorearip.sh
#        hace ping y escribe               interpreta y escribe
#        datosconexion.txt                 estado.txt
#
#  Python solo LEE esos archivos cada segundo y repinta. Asi la ventana nunca
#  se queda bloqueada esperando a la red.
# =============================================================================

# --- Comando de RouterOS que pide estado y trafico de dos interfaces ---------
#  Se comprueba con [:len [/interface find name=...]] que la interfaz exista
#  antes de pedir el trafico, o una interfaz mal escrita hace fallar todo.
def comando_monitor(if1, if2):
    partes = []
    partes.append(':local n1 "' + if1 + '"; :local n2 "' + if2 + '";')
    for numero, var in ((1, "$n1"), (2, "$n2")):
        partes.append(
            ':if ([:len [/interface find name=' + var + ']] > 0) do={'
            '/interface monitor-traffic interface=' + var + ' once do={'
            ':put ("DATO ' + str(numero) + ' " . '
            '[:tostr [/interface get ' + var + ' running]] . " " . '
            '[:tostr $"rx-bits-per-second"] . " " . '
            '[:tostr $"tx-bits-per-second"])'
            '}} else={ :put "DATO ' + str(numero) + ' noexiste 0 0" };')
    return " ".join(partes)


# --- Contenido de los cuatro scripts de monitoreo ----------------------------
#  Se escriben desde Python al arrancar: el proyecto sigue siendo UN SOLO
#  archivo .py, pero el backend de shell queda en disco y es ejecutable.

SH_TEXTO_VERIFICAR_CONEXION = r'''#!/bin/bash
# =============================================================================
#  verificarconexion.sh  --  PRODUCTOR del monitoreo ICMP
# =============================================================================
#  Hace ping al router una vez por segundo y va guardando el resultado.
#
#  Uso:  verificarconexion.sh <ip> <archivo_de_salida>
#
#  Se usa  ping -c 1  (un paquete por vuelta) en vez de un ping continuo, y se
#  recorta el archivo cuando pasa de MAX_LINEAS.
# =============================================================================

IP="$1"
SALIDA="$2"
MAX_LINEAS=300

if [ -z "$SALIDA" ]; then
    echo "Uso: $0 <ip> <archivo_de_salida>" >&2
    exit 1
fi

while true
do
    # -c 1  un solo paquete    -W 2  espera maxima de 2 segundos
    respuesta=$(ping -c 1 -W 2 "$IP" 2>&1 | grep -E "ttl=|TTL=|Unreachable|unreachable|100% packet loss")

    if [ -z "$respuesta" ]; then
        respuesta="sin respuesta de $IP"
    fi

    echo "$(date '+%H:%M:%S') $respuesta" >> "$SALIDA"

    lineas=$(wc -l < "$SALIDA" 2>/dev/null || echo 0)
    if [ "$lineas" -gt "$MAX_LINEAS" ]; then
        tail -n 100 "$SALIDA" > "${SALIDA}.tmp" && mv -f "${SALIDA}.tmp" "$SALIDA"
    fi

    sleep 1
done
'''

SH_TEXTO_MONITOREAR_IP = r'''#!/bin/bash
# =============================================================================
#  monitorearip.sh  --  CONSUMIDOR del monitoreo ICMP
# =============================================================================
#  Lee la ultima linea que dejo verificarconexion.sh y escribe el semaforo:
#
#      estado.txt  ->  1 = el router responde, 0 = no responde
#
#  Uso:  monitorearip.sh <ip> <archivo_datos> <archivo_estado>
#
#  Se busca el patron  ttl=  en cualquier posicion y con cualquier valor, en
#  vez de comparar contra "ttl=64" exacto: el TTL cambia segun el equipo y los
#  saltos de red (64, 63, 128...). Ademas se confirma con un ping directo para
#  no dar por buena una linea antigua que hubiera quedado en el archivo.
# =============================================================================

IP="$1"
DATOS="$2"
ESTADO="$3"

if [ -z "$ESTADO" ]; then
    echo "Uso: $0 <ip> <archivo_datos> <archivo_estado>" >&2
    exit 1
fi

while true
do
    if [ -s "$DATOS" ]; then
        ultima=$(tail -n1 "$DATOS" 2>/dev/null | tr -d '\r')

        # -i para aceptar tanto ttl= como TTL=, segun la version de ping
        if echo "$ultima" | grep -qi "ttl="; then
            if ping -c 1 -W 2 "$IP" > /dev/null 2>&1; then
                echo 1 > "$ESTADO"
            else
                echo 0 > "$ESTADO"
            fi
        else
            # Cubre "Unreachable", "100% packet loss" y "sin respuesta"
            echo 0 > "$ESTADO"
        fi
    fi

    sleep 1
done
'''

SH_TEXTO_VERIFICAR_INTERFACES = r'''#!/bin/bash
# =============================================================================
#  verificarinterfaces.sh  --  PRODUCTOR del monitoreo de interfaces
# =============================================================================
#  Pregunta al router, una vez por segundo, el estado y el trafico de DOS
#  interfaces, y deja la respuesta en un archivo de texto.
#
#  Uso:
#    verificarinterfaces.sh <llave> <usuario> <ip> <timeout> <salida> <comando>
#
#  El <comando> de RouterOS lo arma Python y se pasa como argumento, para no
#  tener que escapar comillas dentro de comillas dentro de bash.
#
#  Formato de cada linea que escribe:
#      DATO <1|2> <true|false> <bits_in> <bits_out>
# =============================================================================

LLAVE="$1"
USUARIO="$2"
IP="$3"
TIMEOUT="${4:-5}"
SALIDA="$5"
CMD="$6"

if [ -z "$CMD" ]; then
    echo "Uso: $0 <llave> <usuario> <ip> <timeout> <salida> <comando>" >&2
    exit 1
fi

TMP="${SALIDA}.tmp"

while true
do
    ssh -T \
        -o BatchMode=yes \
        -o ConnectTimeout="$TIMEOUT" \
        -o LogLevel=ERROR \
        -o StrictHostKeyChecking=accept-new \
        -i "$LLAVE" "$USUARIO@$IP" "$CMD" > "$TMP" 2>/dev/null
    codigo=$?

    # Se comprueba el codigo Y que la respuesta traiga lineas DATO: RouterOS
    # devuelve 0 aunque rechace el comando.
    if [ $codigo -ne 0 ] || ! grep -q "^DATO " "$TMP" 2>/dev/null; then
        printf 'DATO 1 sinconexion 0 0\nDATO 2 sinconexion 0 0\n' > "$TMP"
    fi

    mv -f "$TMP" "$SALIDA"
    sleep 1
done
'''

SH_TEXTO_MONITOREAR_INTERFACES = r'''#!/bin/bash
# =============================================================================
#  monitorearinterfaces.sh  --  CONSUMIDOR del monitoreo de interfaces
# =============================================================================
#  Lee el archivo que deja verificarinterfaces.sh, lo interpreta y escribe:
#
#    - cuatro archivos con el valor INSTANTANEO, que es lo que lee la ventana
#      cada segundo para repintar los paneles:
#          estadoN.txt   ->  1 = UP, 0 = DOWN
#          traficoN.txt  ->  "<bits_in> <bits_out>"
#      Estos se SOBRESCRIBEN: solo interesa el ultimo valor.
#
#    - un REGISTRO HISTORICO en trafico.log, con una linea por interfaz y por
#      segundo, fechada. Este si se acumula, para poder demostrar despues que
#      hubo trafico y a que hora exactamente.
#
#  Uso:
#    monitorearinterfaces.sh <datos> <estado1> <trafico1> <estado2> <trafico2> \
#                            <log> <max_lineas> <nombre_if1> <nombre_if2>
# =============================================================================

DATOS="$1"
ESTADO1="$2"
TRAFICO1="$3"
ESTADO2="$4"
TRAFICO2="$5"
LOG="$6"
MAX_LINEAS="${7:-500}"
NOMBRE1="${8:-interfaz1}"
NOMBRE2="${9:-interfaz2}"

if [ -z "$TRAFICO2" ]; then
    echo "Uso: $0 <datos> <estado1> <trafico1> <estado2> <trafico2> <log> <max> <if1> <if2>" >&2
    exit 1
fi

# Convierte bits por segundo en algo legible.
# Se usa awk y no aritmetica de bash porque bash solo trabaja con enteros:
# no podria calcular "4.82 Mbps", se quedaria en "4 Mbps".
humano() {
    awk -v b="${1:-0}" 'BEGIN{
        if (b+0 >= 1000000000)   printf "%.2f Gbps", b/1000000000;
        else if (b+0 >= 1000000) printf "%.2f Mbps", b/1000000;
        else if (b+0 >= 1000)    printf "%.2f kbps", b/1000;
        else                     printf "%d bps", b+0;
    }'
}

# Escribe el estado, el trafico instantaneo y la linea del registro.
#   $1 = linea leida   $2 = archivo de estado   $3 = archivo de trafico
#   $4 = nombre de la interfaz
procesar() {
    linea="$1"
    archivo_estado="$2"
    archivo_trafico="$3"
    nombre="$4"

    # tr -d '\r' quita el retorno de carro que mete RouterOS al final de
    # cada linea; sin esto, el valor real es "true\r" y nunca compara igual.
    estado=$(echo "$linea" | tr -d '\r' | cut -d " " -f3)
    rx=$(echo "$linea" | tr -d '\r' | cut -d " " -f4)
    tx=$(echo "$linea" | tr -d '\r' | cut -d " " -f5)

    # Las comillas alrededor de $estado son obligatorias: si la variable
    # queda vacia, sin comillas el test da error de sintaxis y el bucle
    # muere en silencio.
    if [ "$estado" = "true" ]; then
        echo 1 > "$archivo_estado"
        echo "${rx:-0} ${tx:-0}" > "$archivo_trafico"
        etiqueta="UP"
    else
        echo 0 > "$archivo_estado"
        echo "0 0" > "$archivo_trafico"
        etiqueta="DOWN"
        rx=0
        tx=0
    fi

    printf '%s  %-10s %-4s  IN %-12s OUT %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$nombre" "$etiqueta" \
        "$(humano "${rx:-0}")" "$(humano "${tx:-0}")" >> "$LOG"
}

# Marca de inicio, para que en el registro se distinga una sesion de otra.
printf '\n===== monitoreo iniciado  %s  (%s y %s) =====\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$NOMBRE1" "$NOMBRE2" >> "$LOG"

while true
do
    if [ -s "$DATOS" ]; then
        linea1=$(grep "^DATO 1" "$DATOS" 2>/dev/null | tail -n1)
        linea2=$(grep "^DATO 2" "$DATOS" 2>/dev/null | tail -n1)

        [ -n "$linea1" ] && procesar "$linea1" "$ESTADO1" "$TRAFICO1" "$NOMBRE1"
        [ -n "$linea2" ] && procesar "$linea2" "$ESTADO2" "$TRAFICO2" "$NOMBRE2"

        # Recorte para que el registro no crezca sin fin. Se escribe aparte
        # y se mueve con mv, atomico, para no leerlo a medio recortar.
        lineas=$(wc -l < "$LOG" 2>/dev/null || echo 0)
        if [ "$lineas" -gt "$MAX_LINEAS" ]; then
            tail -n "$MAX_LINEAS" "$LOG" > "${LOG}.tmp" && mv -f "${LOG}.tmp" "$LOG"
        fi
    fi

    sleep 1
done
'''


def escribir_scripts_monitoreo():
    """Deja los cuatro scripts de monitoreo en backend/, listos para ejecutar.

    Se reescriben en cada arranque para corresponder a la version actual del
    programa, y se les pone permiso de ejecucion desde Python.
    """
    parejas = (
        (SH_VERIFICAR_CONEXION, SH_TEXTO_VERIFICAR_CONEXION),
        (SH_MONITOREAR_IP, SH_TEXTO_MONITOREAR_IP),
        (SH_VERIFICAR_INTERFACES, SH_TEXTO_VERIFICAR_INTERFACES),
        (SH_MONITOREAR_INTERFACES, SH_TEXTO_MONITOREAR_INTERFACES),
    )
    for ruta, texto in parejas:
        try:
            with open(ruta, "w", encoding="utf-8") as pf:
                pf.write(texto)
            os.chmod(ruta, 0o755)
        except OSError:
            pass


def _lanzar(script, *args):
    """Lanza un script de monitoreo en segundo plano, desatendido."""
    cmd = ["bash", script] + [str(a) for a in args]
    try:
        subprocess.Popen(cmd,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


def _matar(script):
    """Mata las instancias de un script de monitoreo.

    El patron entre corchetes  [v]erificar...  evita que pkill se encuentre
    a si mismo en la lista de procesos.
    """
    nombre = os.path.basename(script)
    patron = "[" + nombre[0] + "]" + nombre[1:]
    subprocess.call("pkill -f '" + patron + "'", shell=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def iniciar_monitoreo(interfaz_1, interfaz_2):
    """Arranca los cuatro scripts de monitoreo. Si ya corrian, los reinicia."""
    detener_monitoreo()
    escribir_scripts_monitoreo()

    # Limpiar lecturas viejas para no mostrar el estado de la sesion anterior.
    # Ojo: F_LOG_TRAFICO NO se limpia a proposito, es el registro historico y
    # debe sobrevivir entre sesiones; el propio script lo recorta si crece.
    for archivo in (F_ESTADO_ICMP, F_DATOS_PING, F_DATOS_INTERFACES,
                    F_ESTADO_1, F_ESTADO_2, F_TRAFICO_1, F_TRAFICO_2):
        try:
            with open(archivo, "w", encoding="utf-8") as pf:
                pf.write("")
        except OSError:
            pass

    _lanzar(SH_VERIFICAR_INTERFACES, LLAVE, USUARIO, IP, str(TIMEOUT),
            F_DATOS_INTERFACES, comando_monitor(interfaz_1, interfaz_2))
    _lanzar(SH_MONITOREAR_INTERFACES, F_DATOS_INTERFACES,
            F_ESTADO_1, F_TRAFICO_1, F_ESTADO_2, F_TRAFICO_2,
            F_LOG_TRAFICO, str(MAX_LINEAS_LOG), interfaz_1, interfaz_2)
    _lanzar(SH_VERIFICAR_CONEXION, IP, F_DATOS_PING)
    _lanzar(SH_MONITOREAR_IP, IP, F_DATOS_PING, F_ESTADO_ICMP)


def detener_monitoreo():
    """Detiene todos los scripts de monitoreo."""
    for script in (SH_VERIFICAR_INTERFACES, SH_MONITOREAR_INTERFACES,
                   SH_VERIFICAR_CONEXION, SH_MONITOREAR_IP):
        _matar(script)


def leer_runtime(archivo):
    """Lee un archivo de runtime. Devuelve cadena vacia si aun no existe."""
    try:
        with open(archivo, "r", encoding="utf-8") as pf:
            return pf.read().strip()
    except (IOError, OSError):
        return ""


def leer_log_trafico(ultimas=200):
    """Devuelve las ultimas lineas del registro de trafico, mas nuevas abajo."""
    try:
        with open(F_LOG_TRAFICO, "r", encoding="utf-8") as pf:
            lineas = pf.read().splitlines()
    except (IOError, OSError):
        return ""
    return "\n".join(lineas[-ultimas:])


def formato_trafico(bits):
    """Convierte bits por segundo en un texto legible."""
    try:
        b = int(bits)
    except (ValueError, TypeError):
        return "0 bps"
    if b >= 1000000000:
        return str(round(b / 1000000000.0, 2)) + " Gbps"
    if b >= 1000000:
        return str(round(b / 1000000.0, 2)) + " Mbps"
    if b >= 1000:
        return str(round(b / 1000.0, 2)) + " kbps"
    return str(b) + " bps"


def fraccion_trafico(bits, techo=100000000.0):
    """Devuelve un valor de 0 a 1 para la barra de progreso del trafico.

    Escala logaritmica: el trafico real salta de cientos de bits a varios
    megabits, y en escala lineal la barra quedaria pegada al cero.
    """
    try:
        b = float(bits)
    except (ValueError, TypeError):
        return 0.0
    if b <= 0:
        return 0.0
    import math
    valor = math.log10(b + 1) / math.log10(techo)
    return max(0.0, min(1.0, valor))


# =============================================================================
#  A PARTIR DE AQUI EMPIEZA EL FRONTEND
# =============================================================================
#  POR QUE CUSTOMTKINTER
#  Es tkinter por debajo (o sea que sigue siendo la biblioteca grafica de la
#  biblioteca estandar de Python), pero con widgets redibujados: esquinas
#  redondeadas, tema oscuro, botones con efecto al pasar el raton.
# =============================================================================

import queue
import sys

try:
    import customtkinter as ctk
except ImportError:
    print("=" * 70)
    print(" Falta la biblioteca customtkinter.")
    print("")
    print("   pip install customtkinter")
    print("")
    print(" Y si tkinter tampoco esta instalado (Debian / Ubuntu):")
    print("")
    print("   sudo apt install python3-tk")
    print("=" * 70)
    sys.exit(1)


# =============================================================================
#  SECCION 7 - FRONTEND - TEMA Y COMPONENTES REUTILIZABLES
# =============================================================================

# --- Paleta ------------------------------------------------------------------
# --- Paleta "candy scar" -----------------------------------------------------
#  Los (paleta) salen tal cual de la paleta elegida. Los (derivado) son
#  aclarados u oscurecidos, para el raton encima y el fondo de las tarjetas.
#
#  Criterio de uso del color:
#     turquesa  -> accion normal, sin riesgo   (Guardar, Crear, Consultar)
#     magenta   -> destruir o error            (Eliminar, mensajes de fallo)

C_FONDO = "#17121B"          # (paleta) fondo de la ventana
C_PANEL = "#1E2830"          # (derivado) tarjetas y paneles
C_PANEL_ALT = "#2F4B59"      # (paleta) cabeceras de panel y raton encima
C_BORDE = "#2F4B59"          # (paleta) borde fino de los paneles
C_TEXTO = "#FCFEFD"          # (paleta) texto principal
C_SUAVE = "#97C9EC"          # (paleta) texto secundario
C_ACENTO = "#3F7784"         # (paleta) accion principal
C_ACENTO_H = "#4E93A3"       # (derivado) el anterior, aclarado
C_NEUTRO = "#2F4B59"         # (paleta) botones secundarios
C_NEUTRO_H = "#3F7784"       # (paleta) el anterior, un paso mas claro
C_PELIGRO = "#F9018B"        # (paleta) botones destructivos
C_PELIGRO_H = "#840238"      # (paleta) el anterior, oscurecido
C_MARCA = "#F9018B"          # (paleta) color de marca: titulo y modulo activo
C_ENTRADA = "#17121B"        # (paleta) fondo de las cajas de texto
C_SIDEBAR = "#1A1520"        # (derivado) barra lateral, un paso sobre el fondo

# Colores de ESTADO. Verde y rojo no estan en la paleta a proposito: son los
# dos que la gente lee sin pensar como "funciona" y "no funciona". El verde
# se eligio con el mismo tono frio que los turquesas de la paleta.
C_OK = "#3FD69A"             # interfaz UP, operacion correcta
C_ERROR = "#FE7ED1"          # (paleta) texto de error
C_AVISO = "#F7C5FE"          # (paleta) procesando / esperando datos

REFRESCO_MS = 1000           # cada cuanto se repinta el monitoreo

# Fuentes: se crean en construir_fuentes(), despues de que exista la ventana
# raiz. CTkFont no se puede instanciar antes: necesita el interprete de Tcl
# ya arrancado.
F_TITULO = F_SUBTITULO = F_NORMAL = F_PEQUENA = F_BOTON = None
F_MONO = F_MONO_PEQUENA = F_BADGE = F_SIDEBAR = None
F_BADGE_MEDIA = F_BADGE_CHICA = None


def construir_fuentes():
    global F_TITULO, F_SUBTITULO, F_NORMAL, F_PEQUENA, F_BOTON
    global F_MONO, F_MONO_PEQUENA, F_BADGE, F_SIDEBAR
    global F_BADGE_MEDIA, F_BADGE_CHICA
    F_TITULO = ctk.CTkFont(size=25, weight="bold")
    F_SUBTITULO = ctk.CTkFont(size=17, weight="bold")
    F_NORMAL = ctk.CTkFont(size=15)
    F_PEQUENA = ctk.CTkFont(size=13)
    F_BOTON = ctk.CTkFont(size=14, weight="bold")
    F_MONO = ctk.CTkFont(family="monospace", size=14)
    F_MONO_PEQUENA = ctk.CTkFont(family="monospace", size=12)
    F_BADGE = ctk.CTkFont(size=15, weight="bold")
    # Variantes para cuando la insignia encoge en pantallas bajas: sin
    # ellas, siglas como "SSH" se salian del cuadrado.
    F_BADGE_MEDIA = ctk.CTkFont(size=12, weight="bold")
    F_BADGE_CHICA = ctk.CTkFont(size=10, weight="bold")
    # La descripcion de la barra lateral va un punto por debajo: es texto
    # de apoyo, no debe competir con el titulo.
    F_SIDEBAR = ctk.CTkFont(size=12)


# --- Componentes -------------------------------------------------------------

# --- Iconos del monitoreo ----------------------------------------------------
#  Los semaforos de las interfaces usan imagenes/on.png y imagenes/off.png.
#  Si faltan (o falta Pillow), la aplicacion dibuja un circulo de color.

_CACHE_ICONOS = {}     # hay que guardar las CTkImage: si Python las libera,
                       # Tk se queda sin la imagen y el widget aparece vacio


def icono(nombre, lado):
    """Devuelve una CTkImage del icono pedido, o None si no se puede.

    nombre: "on" (verde), "off" (rojo) o "espera" (el verde en gris).
    """
    clave = (nombre, lado)
    if clave in _CACHE_ICONOS:
        return _CACHE_ICONOS[clave]

    try:
        from PIL import Image
    except ImportError:
        _CACHE_ICONOS[clave] = None
        return None

    archivo = "on.png" if nombre in ("on", "espera") else "off.png"
    ruta = os.path.join(IMAGENES_DIR, archivo)
    if not os.path.isfile(ruta):
        # Tambien se busca al lado del .py, por si se copiaron ahi sueltas.
        ruta = os.path.join(BASE_DIR, archivo)
    if not os.path.isfile(ruta):
        _CACHE_ICONOS[clave] = None
        return None

    try:
        imagen = Image.open(ruta).convert("RGBA")
        if nombre == "espera":
            # Version en gris para "todavia no hay datos": se conserva el
            # canal alfa o se perderian las esquinas transparentes.
            gris = imagen.convert("L").convert("RGBA")
            gris.putalpha(imagen.getchannel("A"))
            imagen = gris
        ctk_img = ctk.CTkImage(light_image=imagen, dark_image=imagen,
                               size=(lado, lado))
    except Exception:
        ctk_img = None

    _CACHE_ICONOS[clave] = ctk_img
    return ctk_img


class Semaforo(ctk.CTkFrame):
    """Indicador de estado Up/Down.

    Muestra el icono si hay imagenes; si no, un circulo de color del mismo
    tamano. Quien lo usa siempre llama a poner(), sin saber cual modo esta
    activo.
    """

    def __init__(self, padre, lado=36):
        super().__init__(padre, fg_color="transparent", width=lado, height=lado)
        self.grid_propagate(False)
        self.pack_propagate(False)

        self.iconos = {e: icono(e, lado) for e in ("on", "off", "espera")}
        self.hay_iconos = all(self.iconos.values())

        if self.hay_iconos:
            self.pieza = ctk.CTkLabel(self, text="", image=self.iconos["espera"])
        else:
            self.pieza = ctk.CTkFrame(self, corner_radius=lado // 2,
                                      fg_color=C_SUAVE)
        self.pieza.pack(fill="both", expand=True)

        self.poner("espera")

    def poner(self, estado):
        """estado: 'up', 'down' o 'espera'."""
        if self.hay_iconos:
            clave = {"up": "on", "down": "off"}.get(estado, "espera")
            self.pieza.configure(image=self.iconos[clave])
        else:
            color = {"up": C_OK, "down": C_ERROR}.get(estado, C_SUAVE)
            self.pieza.configure(fg_color=color)


def tarjeta(padre, **kw):
    """Marco con esquinas redondeadas y borde fino, el bloque visual base."""
    opciones = dict(fg_color=C_PANEL, corner_radius=12,
                    border_width=1, border_color=C_BORDE)
    opciones.update(kw)
    return ctk.CTkFrame(padre, **opciones)


def titulo(padre, texto, fuente=None, color=None):
    fuente = fuente or F_SUBTITULO
    # height ajustado al tamano real de la letra: CTkLabel reserva 28 px
    # fijos y eso abria un hueco entre el titulo y su descripcion.
    return ctk.CTkLabel(padre, text=texto, font=fuente,
                        text_color=color or C_TEXTO, anchor="w",
                        justify="left", height=fuente.cget("size") + 8)


def nota(padre, texto):
    lineas = texto.count("\n") + 1
    return ctk.CTkLabel(padre, text=texto, font=F_PEQUENA, text_color=C_SUAVE,
                        anchor="w", justify="left",
                        height=lineas * (F_PEQUENA.cget("size") + 5))


def boton(padre, texto, comando, tipo="neutro", ancho=0):
    """Boton con los tres estilos que usa la aplicacion."""
    colores = {
        "primario": (C_ACENTO, C_ACENTO_H),
        "neutro": (C_NEUTRO, C_NEUTRO_H),
        "peligro": (C_PELIGRO, C_PELIGRO_H),
    }[tipo]
    return ctk.CTkButton(padre, text=texto, command=comando,
                         fg_color=colores[0], hover_color=colores[1],
                         text_color="#FFFFFF", font=F_BOTON,
                         corner_radius=8, height=36,
                         width=ancho if ancho else 140)


class BotonConfirmar(ctk.CTkButton):
    """Boton destructivo que pide confirmacion SIN abrir ninguna ventana.

    La primera pulsacion lo pone en amarillo con el texto "Pulsa otra vez
    para confirmar"; la segunda ejecuta la accion. A los cinco segundos sin
    la segunda pulsacion, vuelve solo a su estado normal.
    """

    def __init__(self, padre, texto, comando, **kw):
        self._texto_original = texto
        self._comando = comando
        self._armado = False
        self._temporizador = None
        super().__init__(padre, text=texto, command=self._pulsar,
                         fg_color=C_PELIGRO, hover_color=C_PELIGRO_H,
                         text_color="#FFFFFF", font=F_BOTON,
                         corner_radius=8, height=36, **kw)

    def _pulsar(self):
        if self._armado:
            self._desarmar()
            self._comando()
        else:
            self._armado = True
            self.configure(text="Pulsa otra vez para confirmar",
                           fg_color=C_AVISO, hover_color=C_AVISO,
                           text_color="#1A1A1A")
            self._temporizador = self.after(5000, self._desarmar)

    def _desarmar(self):
        if self._temporizador is not None:
            try:
                self.after_cancel(self._temporizador)
            except Exception:
                pass
            self._temporizador = None
        self._armado = False
        self.configure(text=self._texto_original, fg_color=C_PELIGRO,
                       hover_color=C_PELIGRO_H, text_color="#FFFFFF")


class Campo(ctk.CTkFrame):
    """Etiqueta + caja de texto, apiladas. Devuelve la variable asociada."""

    def __init__(self, padre, etiqueta, valor="", pista="", ancho=300):
        super().__init__(padre, fg_color="transparent")
        self.variable = ctk.StringVar(value=valor)
        ctk.CTkLabel(self, text=etiqueta, font=F_PEQUENA, text_color=C_SUAVE,
                     anchor="w", height=F_PEQUENA.cget("size") + 5
                     ).pack(fill="x", padx=2)
        self.entrada = ctk.CTkEntry(self, textvariable=self.variable,
                                    placeholder_text=pista, width=ancho,
                                    height=34, corner_radius=8,
                                    fg_color=C_ENTRADA, border_color=C_BORDE,
                                    text_color=C_TEXTO, font=F_NORMAL)
        self.entrada.pack(fill="x", pady=(3, 0))

    def get(self):
        return self.variable.get()

    def set(self, valor):
        self.variable.set(valor)


class Desplegable(ctk.CTkFrame):
    """Etiqueta + lista desplegable, con boton propio de recarga opcional."""

    def __init__(self, padre, etiqueta, valores=None, ancho=300, editable=False):
        super().__init__(padre, fg_color="transparent")
        self.variable = ctk.StringVar(value="")
        ctk.CTkLabel(self, text=etiqueta, font=F_PEQUENA, text_color=C_SUAVE,
                     anchor="w", height=F_PEQUENA.cget("size") + 5
                     ).pack(fill="x", padx=2)
        self.combo = ctk.CTkComboBox(
            self, variable=self.variable, values=valores or ["(sin datos)"],
            width=ancho, height=34, corner_radius=8,
            state="normal" if editable else "readonly",
            fg_color=C_ENTRADA, border_color=C_BORDE, text_color=C_TEXTO,
            button_color=C_NEUTRO, button_hover_color=C_NEUTRO_H,
            dropdown_fg_color=C_PANEL_ALT, dropdown_text_color=C_TEXTO,
            dropdown_hover_color=C_ACENTO, font=F_NORMAL)
        self.combo.pack(fill="x", pady=(3, 0))

    def get(self):
        valor = self.variable.get()
        return "" if valor.startswith("(") else valor

    def set(self, valor):
        self.variable.set(valor)

    def cargar(self, valores, conservar=True):
        """Rellena la lista. Si estaba vacia, selecciona el primer elemento."""
        anterior = self.variable.get()
        if not valores:
            self.combo.configure(values=["(sin datos)"])
            self.variable.set("(sin datos)")
            return
        self.combo.configure(values=valores)
        if conservar and anterior in valores:
            self.variable.set(anterior)
        else:
            self.variable.set(valores[0])


class PanelResultado(ctk.CTkFrame):
    """Panel donde salen TODOS los mensajes: exito, error y salida del router.

    Sustituye a messagebox. Tres partes: una franja de estado que cambia de
    color segun el resultado, el titulo, y una caja con la respuesta literal
    del router.
    """

    def __init__(self, padre, al_cerrar=None, al_mostrar=None):
        super().__init__(padre, fg_color=C_PANEL, corner_radius=12,
                         border_width=1, border_color=C_BORDE)
        # Dos avisos hacia la pagina que lo contiene: uno para pedir sitio
        # abajo y otro para plegarlo.
        self._al_cerrar = al_cerrar
        self._al_mostrar = al_mostrar
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        cabecera = ctk.CTkFrame(self, fg_color=C_PANEL_ALT, corner_radius=12,
                                height=52)
        cabecera.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 0))
        cabecera.grid_propagate(False)
        cabecera.grid_columnconfigure(1, weight=1)

        self.punto = ctk.CTkFrame(cabecera, width=12, height=12,
                                  corner_radius=6, fg_color=C_SUAVE)
        self.punto.grid(row=0, column=0, padx=(16, 10), pady=20)
        self.punto.grid_propagate(False)

        self.lbl_titulo = ctk.CTkLabel(cabecera, text="RESULTADO",
                                       font=F_SUBTITULO, text_color=C_TEXTO,
                                       anchor="w")
        self.lbl_titulo.grid(row=0, column=1, sticky="w", pady=14)

        self.lbl_hora = ctk.CTkLabel(cabecera, text="", font=F_PEQUENA,
                                     text_color=C_SUAVE)
        self.lbl_hora.grid(row=0, column=2, sticky="e", padx=(16, 8))

        # Boton para plegar el panel y recuperar el alto del formulario.
        ctk.CTkButton(cabecera, text="Ocultar", width=76, height=28,
                      command=self._cerrar, fg_color=C_NEUTRO,
                      hover_color=C_NEUTRO_H, text_color=C_TEXTO,
                      font=F_PEQUENA, corner_radius=6
                      ).grid(row=0, column=3, sticky="e", padx=(0, 14))

        self.caja = ctk.CTkTextbox(self, font=F_MONO, fg_color=C_ENTRADA,
                                   text_color=C_TEXTO, corner_radius=10,
                                   border_width=0, wrap="none", height=210)
        self.caja.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        self.escribir("Aqui aparece la respuesta literal del router.")

    def _cerrar(self):
        if self._al_cerrar is not None:
            self._al_cerrar()

    def _avisar(self):
        if self._al_mostrar is not None:
            self._al_mostrar()

    def escribir(self, texto):
        self.caja.configure(state="normal")
        self.caja.delete("1.0", "end")
        self.caja.insert("1.0", texto)
        self.caja.configure(state="disabled")

    def mostrar(self, ok, titulo_texto, detalle):
        self._avisar()
        color = C_OK if ok else C_ERROR
        self.punto.configure(fg_color=color)
        self.lbl_titulo.configure(text=titulo_texto.upper(), text_color=color)
        self.lbl_hora.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.escribir(detalle or "(sin detalle)")

    def trabajando(self, texto="Hablando con el router, espera un momento..."):
        self._avisar()
        self.punto.configure(fg_color=C_AVISO)
        self.lbl_titulo.configure(text="PROCESANDO", text_color=C_AVISO)
        self.lbl_hora.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.escribir(texto)

    def informar(self, titulo_texto, detalle):
        """Mensaje neutro, ni exito ni error (por ejemplo, una consulta)."""
        self._avisar()
        self.punto.configure(fg_color=C_ACENTO)
        self.lbl_titulo.configure(text=titulo_texto.upper(), text_color=C_ACENTO)
        self.lbl_hora.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.escribir(detalle or "(sin datos)")


# =============================================================================
#  SECCION 8 - FRONTEND - PAGINA BASE
# =============================================================================
#  Todas las paginas de modulo heredan de Pagina. La clase se encarga de la
#  cabecera con el boton "Volver al menu", de la columna del formulario y del
#  panel RESULTADO, para que cada modulo solo tenga que poner sus campos.
# =============================================================================

class ErrorTarea:
    """Envoltorio para una excepcion ocurrida dentro del hilo trabajador.

    Se devuelve como resultado normal en vez de dejar morir el hilo: asi el
    fallo llega a la pagina y se pinta en el panel RESULTADO, en vez de
    perderse en la consola.
    """

    def __init__(self, mensaje):
        self.mensaje = mensaje


class Pagina(ctk.CTkFrame):

    # Cada subclase define estos tres datos
    CLAVE = ""
    TITULO = ""
    DESCRIPCION = ""

    def __init__(self, app, con_resultado=True):
        super().__init__(app.cuerpo, fg_color="transparent")
        self.app = app
        # Banderas propias de la pagina para las lecturas en segundo plano,
        # distintas de operacion_en_curso.
        self._cargando = False
        self._pendiente = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- Cabecera de la pagina ---
        # Ya no lleva boton de volver: la barra lateral esta siempre visible
        # y se cambia de modulo desde ahi.
        cab = ctk.CTkFrame(self, fg_color="transparent")
        cab.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        cab.grid_columnconfigure(0, weight=1)

        titulo(cab, self.TITULO, F_TITULO).grid(row=0, column=0, sticky="w")
        nota(cab, self.DESCRIPCION).grid(row=1, column=0, sticky="w",
                                         pady=(1, 0))

        if con_resultado:
            # El formulario ocupa el ancho completo y el resultado va DEBAJO,
            # no al lado, para no dejar los campos muy estrechos.
            self.izq = ctk.CTkScrollableFrame(
                self, fg_color="transparent",
                scrollbar_button_color=C_NEUTRO,
                scrollbar_button_hover_color=C_NEUTRO_H)
            self.izq.grid(row=1, column=0, sticky="nsew")
            self.izq.grid_columnconfigure(0, weight=1, uniform="form")
            self.izq.grid_columnconfigure(1, weight=1, uniform="form")

            # Las tarjetas se reparten en dos columnas para aprovechar el
            # ancho; bloque() va alternando entre las dos.
            self._columnas = []
            for c in range(2):
                col = ctk.CTkFrame(self.izq, fg_color="transparent")
                col.grid(row=0, column=c, sticky="new",
                         padx=(0, 7) if c == 0 else (7, 0))
                self._columnas.append(col)

            # El panel de resultado empieza OCULTO y aparece solo cuando hay
            # algo que mostrar; mientras tanto, todo el alto es del formulario.
            self.resultado = PanelResultado(self,
                                            al_cerrar=self.ocultar_resultado,
                                            al_mostrar=self.mostrar_resultado)
            self.resultado.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
            self.resultado.grid_remove()
            self._resultado_visible = False
        else:
            self.izq = ctk.CTkFrame(self, fg_color="transparent")
            self.izq.grid(row=1, column=0, sticky="nsew")
            self.izq.grid_columnconfigure(0, weight=1)
            self._columnas = None
            self.resultado = None
            self._resultado_visible = False

        self.construir()

    # --- Mostrar y ocultar el panel de resultado ---

    def mostrar_resultado(self):
        """Hace sitio abajo para el panel de resultado.

        El panel tiene altura fija y el formulario se queda con TODO el
        resto, para no encoger los botones de la primera tarjeta.
        """
        if self.resultado is None or self._resultado_visible:
            return
        self.resultado.grid()
        self.grid_rowconfigure(1, weight=1)   # formulario: se lleva el resto
        self.grid_rowconfigure(2, weight=0)   # resultado: lo que pida
        self._resultado_visible = True

    def ocultar_resultado(self):
        """Devuelve todo el alto al formulario."""
        if self.resultado is None or not self._resultado_visible:
            return
        self.resultado.grid_remove()
        self.grid_rowconfigure(2, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self._resultado_visible = False

    # --- A rellenar por cada modulo ---
    def construir(self):
        pass

    def al_entrar(self):
        """Se llama cada vez que se muestra la pagina."""
        pass

    def al_salir(self):
        """Se llama al abandonar la pagina."""
        pass

    # --- Utilidades comunes a todas las paginas ---

    def bloque(self, titulo_texto, descripcion=""):
        """Crea una tarjeta del formulario en la columna que vaya mas corta.

        Se mide lo que ocupa cada columna y la tarjeta va a la que menos
        tenga. update_idletasks() obliga a Tk a calcular los tamanos en ese
        momento; sin esa llamada las columnas medirian 1 pixel.
        """
        if self._columnas:
            self.izq.update_idletasks()
            alturas = [c.winfo_reqheight() for c in self._columnas]
            destino = self._columnas[0] if alturas[0] <= alturas[1] \
                else self._columnas[1]
        else:
            destino = self.izq
        marco = tarjeta(destino)
        marco.pack(fill="x", pady=(0, 14))
        interior = ctk.CTkFrame(marco, fg_color="transparent")
        interior.pack(fill="x", padx=16, pady=16)
        titulo(interior, titulo_texto).pack(fill="x")
        if descripcion:
            nota(interior, descripcion).pack(fill="x", pady=(1, 0))
        return interior

    def correr(self, funcion, mensaje="Hablando con el router...", despues=None):
        """Manda al hilo trabajador una operacion que CAMBIA algo en el router.

        Se rechaza si ya hay otra operacion de escritura en marcha, para no
        mandar el mismo comando dos veces.

        `despues` es opcional y se ejecuta CUANDO la operacion termina de
        verdad; sirve para recargar desplegables.
        """
        if self.app.operacion_en_curso:
            if self.resultado:
                self.resultado.informar(
                    "Espera",
                    "Hay otra operacion en curso.\n"
                    "En cuanto termine podras lanzar la siguiente.")
            return

        self.app.operacion_en_curso = True
        if self.resultado:
            self.resultado.trabajando(mensaje)

        self.app.encolar(funcion, lambda salida: self._terminar(salida, despues))

    def _terminar(self, salida, despues=None):
        self.app.operacion_en_curso = False

        if isinstance(salida, ErrorTarea):
            salida = (False, "Error inesperado en la aplicacion", salida.mensaje)

        if salida is not None:
            ok, titulo_texto, detalle = salida
            if self.resultado:
                self.resultado.mostrar(ok, titulo_texto, detalle)
            if ok:
                self.app.refrescar_cabecera_async()

        if despues is not None:
            despues()

    def consulta(self, funcion, titulo_texto):
        """Igual que correr(), pero para consultas que solo devuelven texto."""
        self.correr(lambda: (True, titulo_texto, funcion() or "(sin datos)"),
                    "Consultando al router...")

    def leer(self, consulta_backend, pintar, mensaje=None):
        """Encola una consulta de SOLO LECTURA para rellenar los desplegables.

        No usa el candado de operacion_en_curso a proposito: al entrar en una
        pagina se recargan las listas, y si eso lo bloqueara, el primer boton
        que pulsara el usuario se descartaria sin hacer nada.
        """
        if mensaje and self.resultado:
            self.resultado.trabajando(mensaje)

        def cuando(datos):
            if isinstance(datos, ErrorTarea):
                if self.resultado:
                    self.resultado.mostrar(False, "Fallo al leer del router",
                                           datos.mensaje)
                return
            pintar(datos)

        self.app.encolar(consulta_backend, cuando)


# =============================================================================
#  SECCION 8.1 - PAGINA: IDENTIDAD DEL ROUTER
# =============================================================================

class PaginaIdentidad(Pagina):
    CLAVE = "identidad"
    TITULO = "Identidad del router"
    DESCRIPCION = "Asignar y consultar el nombre (identity) del MikroTik."

    def construir(self):
        b = self.bloque("Asignar nombre",
                        "Letras, numeros, punto, guion y guion bajo. Sin espacios.")
        self.c_nombre = Campo(b, "NOMBRE DEL ROUTER", pista="R1-UNAH")
        self.c_nombre.pack(fill="x", pady=(12, 14))

        fila = ctk.CTkFrame(b, fg_color="transparent")
        fila.pack(fill="x")
        boton(fila, "Aplicar nombre", self.aplicar, "primario").pack(side="left")
        boton(fila, "Consultar actual", self.consultar_nombre).pack(side="left",
                                                                    padx=(10, 0))

        info = self.bloque("Como se ejecuta",
                           "Al pulsar Aplicar, la aplicacion escribe el archivo\n"
                           "backend/routername.sh con el nombre dentro y lo lanza\n"
                           "con bash. Puedes abrirlo despues para verlo.")
        nota(info, "ssh -i <llave> admin@<ip> 'system identity set name=<nombre>'"
             ).pack(fill="x", pady=(10, 0))

    def aplicar(self):
        nombre = self.c_nombre.get()
        self.correr(lambda: op_set_nombre(nombre), "Asignando el nombre...")

    def consultar_nombre(self):
        self.consulta(print_identity, "Identidad del router")


# =============================================================================
#  SECCION 8.2 - PAGINA: DIRECCIONES IP
# =============================================================================

class PaginaIP(Pagina):
    CLAVE = "ip"
    TITULO = "Direcciones IP"
    DESCRIPCION = "Crear y eliminar direcciones IP en las interfaces del router."

    def construir(self):
        # --- Crear ---
        b = self.bloque("Crear direccion IP")
        self.c_ip = Campo(b, "DIRECCION CON MASCARA", pista="192.168.60.1/24")
        self.c_ip.pack(fill="x", pady=(12, 10))

        self.d_interfaz = Desplegable(b, "INTERFAZ", editable=True)
        self.d_interfaz.pack(fill="x", pady=(0, 10))

        self.c_comentario = Campo(b, "COMENTARIO (opcional)", pista="LAN de pruebas")
        self.c_comentario.pack(fill="x", pady=(0, 14))

        boton(b, "Crear IP", self.crear, "primario").pack(anchor="w")

        # --- Eliminar ---
        b2 = self.bloque("Eliminar direccion IP",
                         "Se busca por direccion, no por numero de fila, para no\n"
                         "borrar la IP equivocada cuando una interfaz tiene dos.")
        self.d_ips = Desplegable(b2, "DIRECCION EXISTENTE")
        self.d_ips.pack(fill="x", pady=(12, 14))

        fila = ctk.CTkFrame(b2, fg_color="transparent")
        fila.pack(fill="x")
        BotonConfirmar(fila, "Eliminar IP", self.eliminar,
                       width=150).pack(side="left")
        boton(fila, "Actualizar listas", self.recargar).pack(side="left",
                                                             padx=(10, 0))

        # --- Consultas ---
        b3 = self.bloque("Consultar")
        fila2 = ctk.CTkFrame(b3, fg_color="transparent")
        fila2.pack(fill="x", pady=(12, 0))
        boton(fila2, "Ver direcciones", lambda: self.consulta(
            print_ips, "Direcciones IP del router")).pack(side="left")
        boton(fila2, "Ver interfaces", lambda: self.consulta(
            print_interfaces, "Interfaces del router")).pack(side="left",
                                                             padx=(10, 0))

    def al_entrar(self):
        self.recargar(silencioso=True)

    def recargar(self, silencioso=False):
        self.leer(lambda: (get_interfaces(), get_ips_con_interfaz()),
                  lambda datos: self._pintar(datos, silencioso),
                  None if silencioso else "Leyendo interfaces y direcciones...")

    def _pintar(self, datos, silencioso):
        interfaces, ips = datos
        self.d_interfaz.cargar(interfaces)
        self.d_ips.cargar(ips)
        if not silencioso and self.resultado:
            self.resultado.informar(
                "Listas actualizadas",
                "Interfaces encontradas: " + str(len(interfaces)) + "\n" +
                ("\n".join("  " + x for x in interfaces) or "  (ninguna)") +
                "\n\nDirecciones encontradas: " + str(len(ips)) + "\n" +
                ("\n".join("  " + x for x in ips) or "  (ninguna)"))

    def crear(self):
        direccion = self.c_ip.get()
        interfaz = self.d_interfaz.get()
        comentario = self.c_comentario.get()
        self.correr(lambda: op_crear_ip(direccion, interfaz, comentario),
                    "Creando la direccion IP...",
                    despues=lambda: self.recargar(silencioso=True))

    def eliminar(self):
        direccion = self.d_ips.get()
        if not direccion:
            self.resultado.mostrar(False, "Sin seleccion",
                                   "Elige una direccion de la lista.\n"
                                   "Si esta vacia, pulsa 'Actualizar listas'.")
            return
        self.correr(lambda: op_eliminar_ip(direccion), "Eliminando la IP...",
                    despues=lambda: self.recargar(silencioso=True))


# =============================================================================
#  SECCION 8.3 - PAGINA: SERVIDOR DHCP
# =============================================================================

class PaginaDHCP(Pagina):
    CLAVE = "dhcp"
    TITULO = "Servidor DHCP"
    DESCRIPCION = "Crear y eliminar servidores DHCP completos (pool, servidor y red)."

    def construir(self):
        b = self.bloque(
            "Crear servidor DHCP",
            "Se hacen cuatro pasos en orden: IP en la interfaz, pool,\n"
            "servidor y red. Sin IP en la interfaz el servidor nace INVALID.")

        self.d_interfaz = Desplegable(b, "INTERFAZ", editable=True)
        self.d_interfaz.pack(fill="x", pady=(12, 10))

        self.c_ip_if = Campo(b, "IP DE LA INTERFAZ", "192.168.70.1/24")
        self.c_ip_if.pack(fill="x", pady=(0, 10))

        self.c_pool = Campo(b, "NOMBRE DEL POOL", "pool_lan")
        self.c_pool.pack(fill="x", pady=(0, 10))

        self.c_rango = Campo(b, "RANGO DEL POOL",
                             "192.168.70.100-192.168.70.200")
        self.c_rango.pack(fill="x", pady=(0, 10))

        self.c_servidor = Campo(b, "NOMBRE DEL SERVIDOR", "dhcp_lan")
        self.c_servidor.pack(fill="x", pady=(0, 10))

        self.c_red = Campo(b, "RED", "192.168.70.0/24")
        self.c_red.pack(fill="x", pady=(0, 10))

        self.c_gateway = Campo(b, "GATEWAY", "192.168.70.1")
        self.c_gateway.pack(fill="x", pady=(0, 10))

        self.c_dns = Campo(b, "DNS QUE SE REPARTE (opcional)", "8.8.8.8,8.8.4.4")
        self.c_dns.pack(fill="x", pady=(0, 14))

        fila = ctk.CTkFrame(b, fg_color="transparent")
        fila.pack(fill="x")
        boton(fila, "Crear DHCP", self.crear, "primario").pack(side="left")
        boton(fila, "Autocompletar", self.autocompletar).pack(side="left",
                                                              padx=(10, 0))

        # --- Eliminar ---
        b2 = self.bloque(
            "Eliminar servidor DHCP",
            "Se borra en este orden: servidor, pool y red. El pool no se puede\n"
            "borrar mientras un servidor lo este usando.")

        self.d_servidor = Desplegable(b2, "SERVIDOR")
        self.d_servidor.pack(fill="x", pady=(12, 10))
        self.d_pool = Desplegable(b2, "POOL (opcional)")
        self.d_pool.pack(fill="x", pady=(0, 10))
        self.d_red = Desplegable(b2, "RED (opcional)")
        self.d_red.pack(fill="x", pady=(0, 14))

        fila2 = ctk.CTkFrame(b2, fg_color="transparent")
        fila2.pack(fill="x")
        BotonConfirmar(fila2, "Eliminar DHCP", self.eliminar,
                       width=160).pack(side="left")
        boton(fila2, "Actualizar listas", self.recargar).pack(side="left",
                                                              padx=(10, 0))

        # --- Consultas ---
        b3 = self.bloque("Consultar")
        f1 = ctk.CTkFrame(b3, fg_color="transparent")
        f1.pack(fill="x", pady=(12, 8))
        boton(f1, "Servidores", lambda: self.consulta(
            print_dhcp_servers, "Servidores DHCP")).pack(side="left")
        boton(f1, "Pools", lambda: self.consulta(
            print_pools, "Pools de direcciones")).pack(side="left", padx=(10, 0))
        f2 = ctk.CTkFrame(b3, fg_color="transparent")
        f2.pack(fill="x")
        boton(f2, "Redes DHCP", lambda: self.consulta(
            print_dhcp_networks, "Redes DHCP")).pack(side="left")
        boton(f2, "Concesiones", lambda: self.consulta(
            print_leases, "Concesiones DHCP")).pack(side="left", padx=(10, 0))

    def autocompletar(self):
        """Rellena los campos a partir de la IP de la interfaz.

        Ahorra escribir siete campos coherentes entre si: si el gateway no
        pertenece a la red, el servidor DHCP nace invalido.
        """
        ip_if = limpiar(self.c_ip_if.get())
        ok, msg = validar_cidr(ip_if)
        if not ok:
            if self.resultado:
                self.resultado.mostrar(False, "Falta la IP de la interfaz",
                                       "Escribe primero la IP de la interfaz.\n"
                                       "Ejemplo: 192.168.70.1/24\n\n" + msg)
            return

        direccion, mascara = ip_if.split("/", 1)
        partes = direccion.split(".")
        base = ".".join(partes[:3])
        interfaz = self.d_interfaz.get() or "lan"
        sufijo = re.sub(r"[^A-Za-z0-9]", "", interfaz) or "lan"

        self.c_red.set(base + ".0/" + mascara)
        self.c_gateway.set(direccion)
        self.c_rango.set(base + ".100-" + base + ".200")
        self.c_pool.set("pool_" + sufijo)
        self.c_servidor.set("dhcp_" + sufijo)
        if not limpiar(self.c_dns.get()):
            self.c_dns.set("8.8.8.8,8.8.4.4")

        if self.resultado:
            self.resultado.informar(
                "Campos autocompletados",
                "A partir de " + ip_if + " se propuso:\n\n"
                "  Red      : " + self.c_red.get() + "\n"
                "  Gateway  : " + self.c_gateway.get() + "\n"
                "  Rango    : " + self.c_rango.get() + "\n"
                "  Pool     : " + self.c_pool.get() + "\n"
                "  Servidor : " + self.c_servidor.get() + "\n"
                "  DNS      : " + self.c_dns.get() + "\n\n"
                "Revisa los valores y pulsa 'Crear DHCP'.")

    def al_entrar(self):
        self.recargar(silencioso=True)

    def recargar(self, silencioso=False):
        self.leer(lambda: (get_interfaces(), get_dhcp_servers(), get_pools(),
                           get_redes_dhcp()),
                  lambda datos: self._pintar(datos, silencioso),
                  None if silencioso else "Leyendo la configuracion DHCP...")

    def _pintar(self, datos, silencioso):
        interfaces, servidores, pools, redes = datos
        self.d_interfaz.cargar(interfaces)
        self.d_servidor.cargar(servidores)
        self.d_pool.cargar(pools)
        self.d_red.cargar(redes)
        if not silencioso and self.resultado:
            self.resultado.informar(
                "Listas actualizadas",
                "Interfaces : " + (", ".join(interfaces) or "(ninguna)") + "\n"
                "Servidores : " + (", ".join(servidores) or "(ninguno)") + "\n"
                "Pools      : " + (", ".join(pools) or "(ninguno)") + "\n"
                "Redes      : " + (", ".join(redes) or "(ninguna)"))

    def crear(self):
        datos = (self.d_interfaz.get(), self.c_ip_if.get(), self.c_pool.get(),
                 self.c_rango.get(), self.c_servidor.get(), self.c_red.get(),
                 self.c_gateway.get(), self.c_dns.get())
        self.correr(lambda: op_crear_dhcp(*datos), "Creando el servidor DHCP...",
                    despues=lambda: self.recargar(silencioso=True))

    def eliminar(self):
        servidor = self.d_servidor.get()
        if not servidor:
            self.resultado.mostrar(False, "Sin seleccion",
                                   "Elige el servidor DHCP que quieres eliminar.")
            return
        pool, red = self.d_pool.get(), self.d_red.get()
        self.correr(lambda: op_eliminar_dhcp(servidor, pool, red),
                    "Eliminando el servidor DHCP...",
                    despues=lambda: self.recargar(silencioso=True))


# =============================================================================
#  SECCION 8.4 - PAGINA: DNS
# =============================================================================

class PaginaDNS(Pagina):
    CLAVE = "dns"
    TITULO = "Servidores DNS"
    DESCRIPCION = "Configurar y eliminar los servidores DNS del router."

    def construir(self):
        b = self.bloque("Configurar DNS",
                        "Varios servidores separados por coma, sin espacios.")
        self.c_dns = Campo(b, "SERVIDORES DNS", "8.8.8.8,8.8.4.4")
        self.c_dns.pack(fill="x", pady=(12, 12))

        self.v_remoto = ctk.StringVar(value="1")
        ctk.CTkCheckBox(b, text="Permitir peticiones remotas (el router hace de DNS)",
                        variable=self.v_remoto, onvalue="1", offvalue="0",
                        font=F_NORMAL, text_color=C_TEXTO,
                        fg_color=C_ACENTO, hover_color=C_ACENTO_H,
                        border_color=C_BORDE).pack(anchor="w", pady=(0, 14))

        fila = ctk.CTkFrame(b, fg_color="transparent")
        fila.pack(fill="x")
        boton(fila, "Configurar DNS", self.configurar, "primario").pack(side="left")
        boton(fila, "Leer el actual", self.cargar_actual).pack(side="left",
                                                                padx=(10, 0))

        b2 = self.bloque("Eliminar la configuracion DNS",
                         "Deja el router sin servidores DNS y sin peticiones\n"
                         "remotas. Los equipos que dependan del router para\n"
                         "resolver nombres dejaran de navegar.")
        BotonConfirmar(b2, "Eliminar DNS", self.eliminar,
                       width=160).pack(anchor="w", pady=(12, 0))

        b3 = self.bloque("Consultar")
        boton(b3, "Ver configuracion DNS", lambda: self.consulta(
            print_dns, "Configuracion DNS"), ancho=200).pack(anchor="w",
                                                              pady=(12, 0))

    def al_entrar(self):
        self.cargar_actual(silencioso=True)

    def cargar_actual(self, silencioso=False):
        self.leer(lambda: (get_dns_router(), "" if silencioso else print_dns()),
                  lambda datos: self._pintar(datos[0], datos[1], silencioso),
                  None if silencioso else "Leyendo el DNS del router...")

    def _pintar(self, actual, texto, silencioso):
        if actual:
            self.c_dns.set(actual)
        if not silencioso and self.resultado:
            self.resultado.informar("DNS actual del router",
                                    "Servidores: " + (actual or "(ninguno)") +
                                    "\n\n" + texto)

    def configurar(self):
        servidores = self.c_dns.get()
        remoto = self.v_remoto.get() == "1"
        self.correr(lambda: op_configurar_dns(servidores, remoto),
                    "Configurando el DNS...")

    def eliminar(self):
        self.correr(op_eliminar_dns, "Eliminando la configuracion DNS...")


# =============================================================================
#  SECCION 8.5 - PAGINA: RUTAS ESTATICAS
# =============================================================================

class PaginaRutas(Pagina):
    CLAVE = "rutas"
    TITULO = "Rutas estaticas"
    DESCRIPCION = "Crear y eliminar rutas estaticas en la tabla de enrutamiento."

    def construir(self):
        b = self.bloque("Crear ruta estatica")
        self.c_destino = Campo(b, "RED DE DESTINO", "192.168.90.0/24")
        self.c_destino.pack(fill="x", pady=(12, 10))
        self.c_gateway = Campo(b, "GATEWAY", "192.168.56.2")
        self.c_gateway.pack(fill="x", pady=(0, 10))
        self.c_comentario = Campo(b, "COMENTARIO (opcional)", "Ruta hacia sucursal")
        self.c_comentario.pack(fill="x", pady=(0, 14))
        boton(b, "Crear ruta", self.crear, "primario").pack(anchor="w")

        b2 = self.bloque(
            "Eliminar ruta estatica",
            "Solo se listan las rutas con static=yes, o sea las que puso el\n"
            "administrador. Las que el router genera solo para sus interfaces\n"
            "quedan protegidas.")
        self.d_rutas = Desplegable(b2, "RUTA EXISTENTE")
        self.d_rutas.pack(fill="x", pady=(12, 14))

        fila = ctk.CTkFrame(b2, fg_color="transparent")
        fila.pack(fill="x")
        BotonConfirmar(fila, "Eliminar ruta", self.eliminar,
                       width=160).pack(side="left")
        boton(fila, "Actualizar lista", self.recargar).pack(side="left",
                                                             padx=(10, 0))

        b3 = self.bloque("Consultar")
        fila2 = ctk.CTkFrame(b3, fg_color="transparent")
        fila2.pack(fill="x", pady=(12, 0))
        boton(fila2, "Tabla completa", lambda: self.consulta(
            print_rutas, "Tabla de enrutamiento")).pack(side="left")
        boton(fila2, "Solo estaticas", lambda: self.consulta(
            lambda: get_rutas_detalle() or "(ninguna ruta estatica)",
            "Rutas estaticas")).pack(side="left", padx=(10, 0))

    def al_entrar(self):
        self.recargar(silencioso=True)

    def recargar(self, silencioso=False):
        self.leer(lambda: (get_rutas_estaticas_pares(), get_rutas_detalle()),
                  lambda datos: self._pintar(datos, silencioso),
                  None if silencioso else "Leyendo las rutas estaticas...")

    def _pintar(self, datos, silencioso):
        pares, detalle = datos
        # "destino via gateway" para poder elegir la ruta exacta cuando hay
        # varias rutas hacia el mismo destino con gateways distintos.
        self.d_rutas.cargar([destino + " via " + gateway
                            for destino, gateway in pares])
        if not silencioso and self.resultado:
            self.resultado.informar("Rutas estaticas",
                                    detalle or "(no hay rutas estaticas)")

    def crear(self):
        destino = self.c_destino.get()
        gateway = self.c_gateway.get()
        comentario = self.c_comentario.get()
        self.correr(lambda: op_crear_ruta(destino, gateway, comentario),
                    "Creando la ruta estatica...",
                    despues=lambda: self.recargar(silencioso=True))

    def eliminar(self):
        seleccion = self.d_rutas.get()
        if not seleccion or " via " not in seleccion:
            self.resultado.mostrar(False, "Sin seleccion",
                                   "Elige una ruta de la lista.\n"
                                   "Si esta vacia, pulsa 'Actualizar lista'.")
            return
        destino, gateway = seleccion.split(" via ", 1)
        self.correr(lambda: op_eliminar_ruta(destino, gateway),
                    "Eliminando la ruta...",
                    despues=lambda: self.recargar(silencioso=True))


# =============================================================================
#  SECCION 8.6 - PAGINA: MONITOREO DE DOS INTERFACES
# =============================================================================

class PanelInterfaz(ctk.CTkFrame):
    """Bloque visual de UNA interfaz: semaforo, estado y trafico.

    Los widgets se crean UNA sola vez y el refresco solo les cambia el texto
    y el color, para no ir dejando widgets sueltos cada segundo.
    """

    def __init__(self, padre, numero):
        super().__init__(padre, fg_color=C_PANEL, corner_radius=12,
                         border_width=1, border_color=C_BORDE)
        self.grid_columnconfigure(0, weight=1)

        cab = ctk.CTkFrame(self, fg_color="transparent")
        cab.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 0))
        cab.grid_columnconfigure(0, weight=1)

        self.lbl_nombre = ctk.CTkLabel(cab, text="Interfaz " + str(numero),
                                       font=F_SUBTITULO, text_color=C_TEXTO,
                                       anchor="w")
        self.lbl_nombre.grid(row=0, column=0, sticky="w")

        # Semaforo con los iconos on.png / off.png; si faltan, dibuja un
        # circulo de color equivalente.
        self.semaforo = Semaforo(cab, lado=36)
        self.semaforo.grid(row=0, column=1, sticky="e")

        self.lbl_estado = ctk.CTkLabel(self, text="Esperando datos...",
                                       font=ctk.CTkFont(size=30, weight="bold"),
                                       text_color=C_SUAVE, anchor="w")
        self.lbl_estado.grid(row=1, column=0, sticky="w", padx=18, pady=(6, 12))

        self.lbl_rx = ctk.CTkLabel(self, text="Trafico IN      --", font=F_MONO,
                                   text_color=C_TEXTO, anchor="w")
        self.lbl_rx.grid(row=2, column=0, sticky="ew", padx=18)
        self.barra_rx = ctk.CTkProgressBar(self, height=8, corner_radius=4,
                                           fg_color=C_ENTRADA,
                                           progress_color=C_ACENTO)
        self.barra_rx.grid(row=3, column=0, sticky="ew", padx=18, pady=(4, 12))
        self.barra_rx.set(0)

        self.lbl_tx = ctk.CTkLabel(self, text="Trafico OUT     --", font=F_MONO,
                                   text_color=C_TEXTO, anchor="w")
        self.lbl_tx.grid(row=4, column=0, sticky="ew", padx=18)
        self.barra_tx = ctk.CTkProgressBar(self, height=8, corner_radius=4,
                                           fg_color=C_ENTRADA,
                                           progress_color=C_OK)
        self.barra_tx.grid(row=5, column=0, sticky="ew", padx=18, pady=(4, 18))
        self.barra_tx.set(0)

    def pintar(self, nombre, estado, rx, tx):
        self.lbl_nombre.configure(text=nombre)

        if estado == "1":
            self.semaforo.poner("up")
            self.lbl_estado.configure(text="UP", text_color=C_OK)
        elif estado == "0":
            self.semaforo.poner("down")
            self.lbl_estado.configure(text="DOWN", text_color=C_ERROR)
        else:
            self.semaforo.poner("espera")
            self.lbl_estado.configure(text="Esperando...", text_color=C_SUAVE)

        self.lbl_rx.configure(text="Trafico IN      " + formato_trafico(rx))
        self.lbl_tx.configure(text="Trafico OUT     " + formato_trafico(tx))
        self.barra_rx.set(fraccion_trafico(rx))
        self.barra_tx.set(fraccion_trafico(tx))

    def apagar(self, texto="Detenido"):
        self.semaforo.poner("espera")
        self.lbl_estado.configure(text=texto, text_color=C_SUAVE)
        self.lbl_rx.configure(text="Trafico IN      --")
        self.lbl_tx.configure(text="Trafico OUT     --")
        self.barra_rx.set(0)
        self.barra_tx.set(0)


class PaginaMonitoreo(Pagina):
    CLAVE = "monitoreo"
    TITULO = "Monitoreo en tiempo real"
    DESCRIPCION = ("Estado Up/Down y trafico de entrada y salida de dos "
                   "interfaces, refrescado cada segundo.")

    def __init__(self, app):
        self.refresco = {"id": None, "activo": False}
        super().__init__(app, con_resultado=False)

    def construir(self):
        self.izq.grid_columnconfigure(0, weight=1)
        self.izq.grid_columnconfigure(1, weight=1)
        self.izq.grid_rowconfigure(1, weight=3)   # los dos paneles
        self.izq.grid_rowconfigure(3, weight=2)   # el registro de trafico

        # --- Barra de control ---
        barra = tarjeta(self.izq)
        barra.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        interior = ctk.CTkFrame(barra, fg_color="transparent")
        interior.pack(fill="x", padx=18, pady=16)
        interior.grid_columnconfigure(4, weight=1)

        self.d_if1 = Desplegable(interior, "INTERFAZ 1", ancho=190, editable=True)
        self.d_if1.grid(row=0, column=0, sticky="w")
        self.d_if2 = Desplegable(interior, "INTERFAZ 2", ancho=190, editable=True)
        self.d_if2.grid(row=0, column=1, sticky="w", padx=(14, 0))

        self.d_if1.set(INTERFAZ_1)
        self.d_if2.set(INTERFAZ_2)

        self.btn_toggle = ctk.CTkButton(
            interior, text="Iniciar monitoreo", command=self.alternar,
            fg_color=C_ACENTO, hover_color=C_ACENTO_H, text_color="#FFFFFF",
            font=F_BOTON, corner_radius=8, height=34, width=170)
        self.btn_toggle.grid(row=0, column=2, sticky="s", padx=(18, 0), pady=(0, 1))

        ctk.CTkButton(interior, text="Actualizar interfaces",
                      command=self.recargar, fg_color=C_NEUTRO,
                      hover_color=C_NEUTRO_H, text_color=C_TEXTO, font=F_BOTON,
                      corner_radius=8, height=34, width=180
                      ).grid(row=0, column=3, sticky="s", padx=(10, 0), pady=(0, 1))

        self.lbl_servicio = ctk.CTkLabel(interior, text="Servicio detenido",
                                         font=F_NORMAL, text_color=C_SUAVE)
        self.lbl_servicio.grid(row=0, column=4, sticky="se", pady=(0, 6))

        # --- Los dos paneles ---
        self.panel1 = PanelInterfaz(self.izq, 1)
        self.panel1.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        self.panel2 = PanelInterfaz(self.izq, 2)
        self.panel2.grid(row=1, column=1, sticky="nsew", padx=(7, 0))

        # --- Alcance del router por ICMP ---
        icmp = tarjeta(self.izq)
        icmp.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        dentro = ctk.CTkFrame(icmp, fg_color="transparent")
        dentro.pack(fill="x", padx=18, pady=16)
        dentro.grid_columnconfigure(1, weight=1)

        self.punto_icmp = Semaforo(dentro, lado=26)
        self.punto_icmp.grid(row=0, column=0, rowspan=2, padx=(0, 14))

        titulo(dentro, "Alcance del router (ping ICMP a " + IP + ")"
               ).grid(row=0, column=1, sticky="w")
        self.lbl_icmp = ctk.CTkLabel(dentro, text="Servicio detenido",
                                     font=F_NORMAL, text_color=C_SUAVE,
                                     anchor="w")
        self.lbl_icmp.grid(row=1, column=1, sticky="w")

        # --- Registro de trafico en vivo ---
        registro = tarjeta(self.izq)
        registro.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(14, 0))
        registro.grid_columnconfigure(0, weight=1)
        registro.grid_rowconfigure(1, weight=1)

        cabecera_log = ctk.CTkFrame(registro, fg_color="transparent")
        cabecera_log.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 0))
        cabecera_log.grid_columnconfigure(0, weight=1)

        titulo(cabecera_log, "Registro de trafico  (runtime/trafico.log)"
               ).grid(row=0, column=0, sticky="w")
        nota(cabecera_log,
             "Una linea por interfaz y por segundo, con fecha y hora. Se guarda "
             "en disco y se recorta a las ultimas " + str(MAX_LINEAS_LOG) +
             " lineas.").grid(row=1, column=0, sticky="w")

        ctk.CTkButton(cabecera_log, text="Abrir registro completo",
                      command=self.ver_log_completo, fg_color=C_NEUTRO,
                      hover_color=C_NEUTRO_H, text_color=C_TEXTO, font=F_BOTON,
                      corner_radius=8, height=32, width=190
                      ).grid(row=0, column=1, rowspan=2, sticky="e")

        self.caja_log = ctk.CTkTextbox(registro, font=F_MONO, fg_color=C_ENTRADA,
                                       text_color=C_TEXTO, corner_radius=10,
                                       border_width=0, wrap="none", height=118)
        self.caja_log.grid(row=1, column=0, sticky="nsew", padx=14, pady=(10, 14))
        self._escribir_log("(el registro se llena mientras el monitoreo esta activo)")

        self.lbl_explicacion = ctk.CTkLabel(
            dentro, anchor="e", justify="right", font=F_PEQUENA,
            text_color=C_SUAVE,
            text=("Los datos los recogen cuatro scripts de bash en segundo plano.\n"
                  "La ventana solo lee runtime/*.txt cada segundo, asi que no se\n"
                  "congela aunque el router tarde en responder."))
        self.lbl_explicacion.grid(row=0, column=2, rowspan=2, sticky="e")

    def al_entrar(self):
        self.recargar(silencioso=True)

    def al_salir(self):
        # Se detiene siempre el servicio al salir, para no dejar cuatro
        # procesos de bash corriendo sin que nadie los mire.
        if self.refresco["activo"]:
            self.detener()

    def recargar(self, silencioso=False):
        self.leer(get_interfaces, self._pintar)

    def _pintar(self, interfaces):
        if not interfaces:
            return
        actual1, actual2 = self.d_if1.get(), self.d_if2.get()
        self.d_if1.combo.configure(values=interfaces)
        self.d_if2.combo.configure(values=interfaces)
        if actual1 not in interfaces:
            self.d_if1.set(interfaces[0])
        if actual2 not in interfaces:
            self.d_if2.set(interfaces[1] if len(interfaces) > 1 else interfaces[0])

    # --- Registro de trafico ---

    def _escribir_log(self, texto):
        self.caja_log.configure(state="normal")
        self.caja_log.delete("1.0", "end")
        self.caja_log.insert("1.0", texto)
        self.caja_log.see("end")        # siempre pegado a lo mas reciente
        self.caja_log.configure(state="disabled")

    def ver_log_completo(self):
        """Muestra las ultimas 500 lineas dentro de la propia ventana.

        No abre el archivo con un programa externo ni saca un dialogo: se
        vuelca aqui mismo, como el resto de la aplicacion.
        """
        texto = leer_log_trafico(MAX_LINEAS_LOG)
        if not texto:
            texto = ("Todavia no hay registro.\n\n"
                     "Se crea al iniciar el monitoreo, en:\n  " + F_LOG_TRAFICO)
        self._escribir_log(texto)

    # --- Arranque y parada ---

    def alternar(self):
        if self.refresco["activo"]:
            self.detener()
        else:
            self.iniciar()

    def iniciar(self):
        if1, if2 = self.d_if1.get(), self.d_if2.get()

        for valor in (if1, if2):
            ok, msg = validar_interfaz(valor)
            if not ok:
                self.lbl_servicio.configure(text=msg.replace("\n", " "),
                                            text_color=C_ERROR)
                return
        if if1 == if2:
            self.lbl_servicio.configure(text="Elige dos interfaces distintas",
                                        text_color=C_ERROR)
            return

        iniciar_monitoreo(if1, if2)
        self.refresco["activo"] = True
        self.refrescar()

        self.btn_toggle.configure(text="Detener monitoreo", fg_color=C_PELIGRO,
                                  hover_color=C_PELIGRO_H)
        self.lbl_servicio.configure(text="Servicio activo", text_color=C_OK)

    def detener(self):
        self.refresco["activo"] = False
        if self.refresco["id"] is not None:
            try:
                self.after_cancel(self.refresco["id"])
            except Exception:
                pass
            self.refresco["id"] = None

        detener_monitoreo()

        self.btn_toggle.configure(text="Iniciar monitoreo", fg_color=C_ACENTO,
                                  hover_color=C_ACENTO_H)
        self.lbl_servicio.configure(text="Servicio detenido", text_color=C_SUAVE)
        self.panel1.apagar()
        self.panel2.apagar()
        self.punto_icmp.poner("espera")
        self.lbl_icmp.configure(text="Servicio detenido", text_color=C_SUAVE)

    def refrescar(self):
        """Lee los archivos de runtime/ y repinta. No toca la red."""
        if not self.refresco["activo"]:
            return

        for panel, f_estado, f_trafico, nombre in (
                (self.panel1, F_ESTADO_1, F_TRAFICO_1, self.d_if1.get()),
                (self.panel2, F_ESTADO_2, F_TRAFICO_2, self.d_if2.get())):
            trafico = leer_runtime(f_trafico).split()
            rx = trafico[0] if len(trafico) > 0 else "0"
            tx = trafico[1] if len(trafico) > 1 else "0"
            panel.pintar(nombre, leer_runtime(f_estado), rx, tx)

        icmp = leer_runtime(F_ESTADO_ICMP)
        if icmp == "1":
            self.punto_icmp.poner("up")
            self.lbl_icmp.configure(text="EL ROUTER RESPONDE", text_color=C_OK)
        elif icmp == "0":
            self.punto_icmp.poner("down")
            self.lbl_icmp.configure(text="EL ROUTER NO RESPONDE",
                                    text_color=C_ERROR)
        else:
            self.punto_icmp.poner("espera")
            self.lbl_icmp.configure(text="Esperando datos...", text_color=C_AVISO)

        # Ultimas lineas del registro, para que se vea crecer en vivo.
        self._escribir_log(leer_log_trafico(40) or
                           "(esperando la primera lectura...)")

        self.refresco["id"] = self.after(REFRESCO_MS, self.refrescar)


# =============================================================================
#  SECCION 8.7 - PAGINA: RESPALDOS
# =============================================================================

class PaginaRespaldos(Pagina):
    CLAVE = "respaldos"
    TITULO = "Respaldos (Backup)"
    DESCRIPCION = "Crear respaldos del router, traerlos al PC y listarlos."

    def construir(self):
        b = self.bloque(
            "Crear respaldo",
            "El router guarda el archivo y despues se trae al PC con scp. Se\n"
            "espera a que el archivo aparezca de verdad y se comprueba que no\n"
            "llegue vacio, en vez de esperar un numero fijo de segundos.")
        boton(b, "Crear respaldo ahora", self.crear, "primario", ancho=200
              ).pack(anchor="w", pady=(12, 0))

        b2 = self.bloque("Respaldos guardados en este equipo")
        self.d_respaldos = Desplegable(b2, "ARCHIVO")
        self.d_respaldos.pack(fill="x", pady=(12, 14))

        fila = ctk.CTkFrame(b2, fg_color="transparent")
        fila.pack(fill="x")
        boton(fila, "Actualizar lista", self.recargar).pack(side="left")
        BotonConfirmar(fila, "Eliminar del PC", self.eliminar,
                       width=170).pack(side="left", padx=(10, 0))

        b3 = self.bloque("Listar")
        fila2 = ctk.CTkFrame(b3, fg_color="transparent")
        fila2.pack(fill="x", pady=(12, 0))
        boton(fila2, "Listar en el PC", lambda: self.consulta(
            listar_respaldos_texto, "Respaldos en el PC")).pack(side="left")
        boton(fila2, "Listar en el router", lambda: self.consulta(
            print_backups_router, "Respaldos en el router")).pack(side="left",
                                                                   padx=(10, 0))

    def al_entrar(self):
        self.recargar(silencioso=True)

    def recargar(self, silencioso=False):
        archivos = listar_respaldos()
        self.d_respaldos.cargar(archivos)
        if not silencioso and self.resultado:
            self.resultado.informar("Respaldos", listar_respaldos_texto())

    def crear(self):
        self.correr(op_crear_respaldo,
                    "Creando el respaldo y trayendolo al PC...\n"
                    "Esto puede tardar unos segundos.",
                    despues=lambda: self.recargar(silencioso=True))

    def eliminar(self):
        nombre = self.d_respaldos.get()
        if not nombre:
            self.resultado.mostrar(False, "Sin seleccion",
                                   "Elige un respaldo de la lista.")
            return
        ok, titulo_texto, detalle = op_eliminar_respaldo(nombre)
        self.resultado.mostrar(ok, titulo_texto, detalle)
        self.recargar(silencioso=True)


# =============================================================================
#  SECCION 8.8 - PAGINA: CONEXION Y LLAVES SSH
# =============================================================================

class PaginaConexion(Pagina):
    CLAVE = "conexion"
    TITULO = "Conexion y llaves SSH"
    DESCRIPCION = ("Datos del router y los tres pasos de la autenticacion por "
                   "llave, sin tocar la terminal.")

    def construir(self):
        # ------------------------------------------------ datos del router --
        b = self.bloque("Datos del router",
                        "Se guardan en conexion.ini, al lado de este programa,\n"
                        "asi que no hay que editar el codigo para cambiar de\n"
                        "router ni para que lo use otro companero.")
        self.c_ip = Campo(b, "IP DEL ROUTER", IP, "192.168.56.10")
        self.c_ip.pack(fill="x", pady=(12, 10))
        self.c_usuario = Campo(b, "USUARIO SSH", USUARIO, "admin")
        self.c_usuario.pack(fill="x", pady=(0, 10))
        self.c_llave = Campo(b, "RUTA DE LA LLAVE PRIVADA", LLAVE,
                             "~/.ssh/mikrotik_tea_key")
        self.c_llave.pack(fill="x", pady=(0, 14))

        fila = ctk.CTkFrame(b, fg_color="transparent")
        fila.pack(fill="x")
        boton(fila, "Guardar", self.guardar, "primario").pack(side="left")
        boton(fila, "Probar conexion", self.probar).pack(side="left", padx=(10, 0))

        # -------------------------------------------- instalacion en un paso -
        b2 = self.bloque(
            "Instalar la llave en el router",
            "Crea el par de llaves, sube la publica y la importa en RouterOS.\n"
            "La contrasena solo hace falta aqui: se usa en memoria, no se\n"
            "guarda en ningun archivo y no aparece en la linea de comandos.")

        self.c_password = Campo(b2, "CONTRASENA DEL ROUTER",
                                pista="se usa una vez y se olvida")
        self.c_password.entrada.configure(show="*")
        self.c_password.pack(fill="x", pady=(12, 10))

        ctk.CTkLabel(b2, text="TAMANO DE LA LLAVE", font=F_PEQUENA,
                     text_color=C_SUAVE, anchor="w").pack(fill="x", padx=2)
        self.v_bits = ctk.StringVar(value="2048 bits")
        ctk.CTkSegmentedButton(
            b2, values=["2048 bits", "4096 bits"], variable=self.v_bits,
            font=F_NORMAL, height=32, corner_radius=8,
            fg_color=C_ENTRADA, selected_color=C_ACENTO,
            selected_hover_color=C_ACENTO_H, unselected_color=C_ENTRADA,
            unselected_hover_color=C_NEUTRO, text_color=C_TEXTO,
            ).pack(fill="x", pady=(4, 10))

        self.v_sobrescribir = ctk.StringVar(value="0")
        ctk.CTkCheckBox(b2, text="Sobrescribir la llave si ya existe",
                        variable=self.v_sobrescribir, onvalue="1", offvalue="0",
                        font=F_NORMAL, text_color=C_TEXTO, fg_color=C_ACENTO,
                        hover_color=C_ACENTO_H, border_color=C_BORDE
                        ).pack(anchor="w", pady=(0, 14))

        boton(b2, "Instalar llave (los 3 pasos)", self.instalar_todo,
              "primario", ancho=250).pack(anchor="w")

        # ---------------------------------------------- pasos por separado --
        b3 = self.bloque(
            "Los mismos pasos, uno a uno",
            "Utiles para saber donde se atasco si algo falla.")

        f1 = ctk.CTkFrame(b3, fg_color="transparent")
        f1.pack(fill="x", pady=(12, 8))
        boton(f1, "1. Crear par de llaves", self.generar,
              ancho=200).pack(side="left")
        boton(f1, "2. Subir la publica", self.subir,
              ancho=180).pack(side="left", padx=(10, 0))

        f2 = ctk.CTkFrame(b3, fg_color="transparent")
        f2.pack(fill="x")
        boton(f2, "3. Importar en RouterOS", self.importar,
              ancho=210).pack(side="left")
        boton(f2, "Arreglar permisos", self.permisos,
              ancho=170).pack(side="left", padx=(10, 0))

        # ------------------------------------------------------- diagnostico -
        b4 = self.bloque("Comprobar")
        f3 = ctk.CTkFrame(b4, fg_color="transparent")
        f3.pack(fill="x", pady=(12, 8))
        boton(f3, "Ver estado", self.ver_estado).pack(side="left")
        boton(f3, "Llaves en el router", lambda: self.consulta(
            listar_llaves_router, "Llaves registradas en el router"),
            ancho=180).pack(side="left", padx=(10, 0))

        f4 = ctk.CTkFrame(b4, fg_color="transparent")
        f4.pack(fill="x")
        boton(f4, "Ver mi llave publica / hacerlo a mano",
              self.ver_publica, ancho=300).pack(side="left")

    # ------------------------------------------------------------- acciones --

    def al_entrar(self):
        self.c_ip.set(IP)
        self.c_usuario.set(USUARIO)
        self.c_llave.set(LLAVE)

    def _bits(self):
        return 4096 if self.v_bits.get().startswith("4096") else 2048

    def guardar(self):
        ip, usuario, llave = (self.c_ip.get(), self.c_usuario.get(),
                              self.c_llave.get())
        # despues=... refresca el pie de la barra lateral con la ruta nueva.
        self.correr(lambda: op_guardar_conexion(ip, usuario, llave),
                    "Guardando conexion.ini...",
                    despues=self.app.refrescar_llave)

    def probar(self):
        self.correr(op_probar_conexion, "Probando a entrar con la llave...")

    def ver_estado(self):
        self.consulta(estado_llaves_texto, "Estado de la autenticacion")

    def ver_publica(self):
        # No toca la red, se puede responder al instante.
        if self.resultado:
            self.resultado.informar("Llave publica", texto_llave_publica())

    def permisos(self):
        self.correr(op_arreglar_permisos, "Cambiando los permisos...",
                    despues=self.app.refrescar_llave)

    def generar(self):
        bits, sobrescribir = self._bits(), self.v_sobrescribir.get() == "1"
        self.correr(lambda: op_generar_llaves(bits, sobrescribir),
                    "Generando el par de llaves con ssh-keygen...\n"
                    "Con 4096 bits puede tardar unos segundos.",
                    despues=self.app.refrescar_llave)

    def subir(self):
        password = self.c_password.get()
        self.correr(lambda: op_subir_llave(password),
                    "Subiendo la llave publica al router con scp...")

    def importar(self):
        password = self.c_password.get()
        self.correr(lambda: op_importar_llave(password),
                    "Importando la llave dentro de RouterOS...")

    def instalar_todo(self):
        password = self.c_password.get()
        bits, sobrescribir = self._bits(), self.v_sobrescribir.get() == "1"
        self.correr(
            lambda: op_instalar_llave_completa(password, bits, sobrescribir),
            "Instalando la llave: crear, subir e importar...\n"
            "Esto puede tardar hasta medio minuto.",
            despues=self.app.refrescar_llave)


# =============================================================================
#  SECCION 8.9 - PAGINA: MENU PRINCIPAL
# =============================================================================

MODULOS = [
    ("ID",  "identidad", "Identidad",       "Nombre del router",            "#F9018B"),
    ("IP",  "ip",        "Direcciones IP",  "Crear y eliminar IPs",         "#FE7ED1"),
    ("DH",  "dhcp",      "Servidor DHCP",   "Pool, servidor y red",         "#F7C5FE"),
    ("DN",  "dns",       "Servidores DNS",  "Configurar y eliminar",        "#97C9EC"),
    ("RT",  "rutas",     "Rutas estaticas", "Tabla de enrutamiento",        "#3F7784"),
    ("MO",  "monitoreo", "Monitoreo",       "Dos interfaces en vivo",       "#3FD69A"),
    ("BK",  "respaldos", "Respaldos",       "Crear, traer y listar",        "#840238"),
    ("SSH", "conexion",  "Conexion",        "Datos del router y llaves",    "#2F4B59"),
]


def texto_sobre(color_fondo):
    """Elige texto negro o blanco segun lo claro que sea el fondo.

    Usa la formula de luminancia percibida, no la media de los tres canales.
    """
    color_fondo = color_fondo.lstrip("#")
    r, g, b = (int(color_fondo[k:k + 2], 16) for k in (0, 2, 4))
    luminancia = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#17121B" if luminancia > 0.6 else "#FCFEFD"


class BotonModulo(ctk.CTkFrame):
    """Una entrada de la barra lateral.

    Estructura de tres columnas:
        [barra] [insignia] [titulo / descripcion]
    """

    ALTO = 58

    def __init__(self, padre, sigla, titulo_texto, descripcion, color, comando):
        super().__init__(padre, fg_color="transparent", corner_radius=10,
                         height=self.ALTO)
        self.comando = comando
        self.activo = False
        self.grid_propagate(False)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Barra de seleccion. Ocupa sitio siempre, se pinte o no, para que
        # el contenido no se desplace al seleccionar.
        self.marca = ctk.CTkFrame(self, width=4, height=34, corner_radius=2,
                                  fg_color="transparent")
        self.marca.grid(row=0, column=0, padx=(5, 0))
        self.marca.grid_propagate(False)

        self.insignia = ctk.CTkFrame(self, width=40, height=40,
                                     corner_radius=12, fg_color=color)
        self.insignia.grid(row=0, column=1, padx=(9, 12))
        self.insignia.grid_propagate(False)
        self.lbl_sigla = ctk.CTkLabel(self.insignia, text=sigla, font=F_BADGE,
                                      text_color=texto_sobre(color))
        self.lbl_sigla.place(relx=0.5, rely=0.5, anchor="center")

        # Los dos textos van en su propio marco centrado verticalmente: con
        # grid a dos filas quedaban pegados al subir el tamano de la letra.
        textos = ctk.CTkFrame(self, fg_color="transparent")
        textos.grid(row=0, column=2, sticky="w", padx=(0, 10))

        self.lbl_titulo = ctk.CTkLabel(textos, text=titulo_texto, font=F_BOTON,
                                       text_color=C_TEXTO, anchor="w",
                                       height=19)
        self.lbl_titulo.pack(anchor="w")

        self.lbl_desc = ctk.CTkLabel(textos, text=descripcion, font=F_SIDEBAR,
                                     text_color=C_SUAVE, anchor="w", height=16)
        self.lbl_desc.pack(anchor="w", pady=(2, 0))

        for w in (self, self.marca, self.insignia, self.lbl_sigla,
                  textos, self.lbl_titulo, self.lbl_desc):
            w.bind("<Button-1>", lambda _e: self.comando())
            w.bind("<Enter>", self._entrar)
            w.bind("<Leave>", self._salir)
            w.configure(cursor="hand2")

    def ajustar(self, alto):
        """Adapta la entrada al alto que le toca.

        Por debajo de cierto punto los dos textos ya no caben, asi que se
        esconde la descripcion y se deja solo el nombre. Es preferible a
        encogerlos hasta que se solapen o poner scroll en la barra lateral.
        """
        alto = int(alto)
        self.configure(height=alto)

        quiere_descripcion = alto >= 46
        visible = self.lbl_desc.winfo_manager() != ""
        if quiere_descripcion and not visible:
            self.lbl_desc.pack(anchor="w", pady=(2, 0))
        elif not quiere_descripcion and visible:
            self.lbl_desc.pack_forget()

        # La insignia tambien encoge, si no se comeria toda la fila
        if alto >= 52:
            lado, fuente = 40, F_BADGE
        elif alto >= 44:
            lado, fuente = 34, F_BADGE_MEDIA
        else:
            lado, fuente = 28, F_BADGE_CHICA
        # La insignia nunca puede ser mas alta que la fila: si lo es,
        # sobresale y se come la separacion entre modulos.
        lado = min(lado, max(16, alto - 6))
        if lado <= 30:
            fuente = F_BADGE_CHICA
        self.insignia.configure(width=lado, height=lado)
        self.lbl_sigla.configure(font=fuente)
        self.marca.configure(height=max(18, alto - 24))

    def _entrar(self, _e=None):
        if not self.activo:
            self.configure(fg_color=C_PANEL)

    def _salir(self, _e=None):
        if not self.activo:
            self.configure(fg_color="transparent")

    def marcar(self, activo):
        """Resalta el modulo que se esta viendo ahora mismo."""
        self.activo = activo
        self.configure(fg_color=C_PANEL_ALT if activo else "transparent")
        self.marca.configure(fg_color=C_MARCA if activo else "transparent")
        self.lbl_titulo.configure(text_color=C_TEXTO)
        self.lbl_desc.configure(text_color=C_TEXTO if activo else C_SUAVE)


class PaginaInicio(Pagina):
    """Pantalla de bienvenida: estado del router y como esta hecho el programa."""

    CLAVE = "inicio"
    TITULO = "Control MikroTik Router"
    DESCRIPCION = ("Elige un modulo en la barra de la izquierda. "
                   "El panel de resultado aparece abajo cuando haga falta.")

    def __init__(self, app):
        super().__init__(app, con_resultado=False)

    def construir(self):
        self.izq.grid_rowconfigure(2, weight=1)

        # --- Estado del router ---
        estado = tarjeta(self.izq)
        estado.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        dentro = ctk.CTkFrame(estado, fg_color="transparent")
        dentro.pack(fill="x", padx=20, pady=18)
        dentro.grid_columnconfigure(1, weight=1)

        self.semaforo = Semaforo(dentro, lado=40)
        self.semaforo.grid(row=0, column=0, rowspan=2, padx=(0, 16))

        self.lbl_estado = ctk.CTkLabel(dentro, text="Comprobando el router...",
                                       font=F_SUBTITULO, text_color=C_TEXTO,
                                       anchor="w")
        self.lbl_estado.grid(row=0, column=1, sticky="w")
        self.lbl_detalle = ctk.CTkLabel(dentro, text=USUARIO + "@" + IP,
                                        font=F_PEQUENA, text_color=C_SUAVE,
                                        anchor="w")
        self.lbl_detalle.grid(row=1, column=1, sticky="w")

        ctk.CTkButton(dentro, text="Comprobar de nuevo",
                      command=self.app.refrescar_cabecera_async,
                      fg_color=C_ACENTO, hover_color=C_ACENTO_H,
                      text_color="#FFFFFF", font=F_BOTON, corner_radius=8,
                      width=180, height=36).grid(row=0, column=2, rowspan=2,
                                                 sticky="e")

        # --- Arquitectura ---
        arq = tarjeta(self.izq)
        arq.grid(row=1, column=0, sticky="ew")
        cuerpo = ctk.CTkFrame(arq, fg_color="transparent")
        cuerpo.pack(fill="x", padx=20, pady=18)
        titulo(cuerpo, "Como funciona por dentro").pack(fill="x")
        nota(cuerpo,
             "Frontend (CustomTkinter)  ->  Backend (Python)  ->  script .sh  "
             "->  ssh  ->  RouterOS\n\n"
             "Cada boton valida lo que escribiste, escribe un archivo .sh en "
             "backend/ con esos\n"
             "parametros dentro, lo ejecuta con bash y despues le vuelve a "
             "preguntar al router\n"
             "si el cambio quedo aplicado de verdad.\n\n"
             "Los .sh quedan en disco: se pueden abrir y ejecutar a mano desde "
             "la terminal."
             ).pack(fill="x", pady=(6, 0))

    def al_entrar(self):
        self.app.refrescar_cabecera_async()

    def pintar_estado(self, ok, nombre):
        self.semaforo.poner("up" if ok else "down")
        if ok:
            self.lbl_estado.configure(text=nombre or "Router conectado",
                                      text_color=C_OK)
        else:
            self.lbl_estado.configure(text="Sin conexion con el router",
                                      text_color=C_ERROR)
        self.lbl_detalle.configure(text=USUARIO + "@" + IP)


class Aplicacion(ctk.CTk):
    """Ventana unica. El contenido se cambia; no se abren ventanas nuevas."""

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")

        self.title("CONTROL MIKROTIK ROUTER")
        self.configure(fg_color=C_FONDO)
        self._ajustar_tamano()

        construir_fuentes()

        # --- Un solo hilo trabajador, con cola ---
        # Todo lo que habla con el router pasa por aqui:
        #   - la ventana nunca se congela esperando a la red
        #   - nunca hay dos sesiones ssh pisandose los scripts .sh
        #   - nada se descarta: se ejecuta todo, en orden
        self.cola = queue.Queue()
        self.operacion_en_curso = False   # solo para las de escritura
        self.vivo = True
        threading.Thread(target=self._bucle_trabajador, daemon=True).start()

        self.pagina_actual = None
        self.botones_modulo = {}

        # Columna 0: barra lateral fija.  Columna 1: la pagina que toque.
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._construir_sidebar()

        self.cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        self.cuerpo.grid(row=0, column=1, sticky="nsew", padx=(20, 22),
                         pady=(20, 24))
        self.cuerpo.grid_columnconfigure(0, weight=1)
        self.cuerpo.grid_rowconfigure(0, weight=1)

        # Todas las paginas se crean UNA vez y se muestran u ocultan: lo que
        # el usuario haya escrito no se pierde al ir y volver.
        self.paginas = {}
        for clase in (PaginaInicio, PaginaIdentidad, PaginaIP, PaginaDHCP,
                      PaginaDNS, PaginaRutas, PaginaMonitoreo, PaginaRespaldos,
                      PaginaConexion):
            pagina = clase(self)
            self.paginas[pagina.CLAVE] = pagina

        self.ir_a("inicio")

        self.protocol("WM_DELETE_WINDOW", self.al_cerrar)
        self.after(400, self.refrescar_cabecera_async)

    def _ajustar_tamano(self):
        """Elige el tamano de la ventana sin salirse de la pantalla.

        Se recorta a lo que de verdad cabe, dejando margen para la barra de
        tareas, y se centra.
        """
        ancho_deseado, alto_deseado = 1280, 860
        margen_inferior = 90                # barra de tareas y bordes

        ancho_pantalla = self.winfo_screenwidth()
        alto_pantalla = self.winfo_screenheight()

        ancho = min(ancho_deseado, ancho_pantalla - 60)
        alto = min(alto_deseado, alto_pantalla - margen_inferior)

        x = max(0, (ancho_pantalla - ancho) // 2)
        y = max(0, (alto_pantalla - alto - margen_inferior) // 2)

        self.geometry("%dx%d+%d+%d" % (ancho, alto, x, y))
        self.minsize(min(1000, ancho), min(620, alto))

    # --- Barra lateral -------------------------------------------------------

    def _construir_sidebar(self):
        self._barra = barra = ctk.CTkFrame(self, fg_color=C_SIDEBAR,
                                           corner_radius=0, width=262)
        barra.grid(row=0, column=0, sticky="nsw")
        barra.grid_propagate(False)
        barra.grid_columnconfigure(0, weight=1)
        barra.grid_rowconfigure(1, weight=1)

        # --- Marca, pulsable para volver al inicio ---
        self._marca_barra = marca = ctk.CTkFrame(barra, fg_color="transparent")
        marca.grid(row=0, column=0, sticky="ew", padx=16, pady=(20, 14))

        lbl1 = ctk.CTkLabel(marca, text="CONTROL MIKROTIK", font=F_SUBTITULO,
                            text_color=C_MARCA, anchor="w")
        lbl1.pack(anchor="w")
        lbl2 = ctk.CTkLabel(marca, text="Administracion por SSH desde Linux",
                            font=F_PEQUENA, text_color=C_SUAVE, anchor="w")
        lbl2.pack(anchor="w")
        for w in (marca, lbl1, lbl2):
            w.bind("<Button-1>", lambda _e: self.ir_a("inicio"))
            w.configure(cursor="hand2")

        # --- Los modulos ---
        # Marco normal, sin scroll: se les reparte el hueco disponible en
        # _ajustar_alto_lista().
        self._caja_lista = ctk.CTkFrame(barra, fg_color="transparent")
        self._caja_lista.grid(row=1, column=0, sticky="nsew", padx=8)
        self._caja_lista.grid_propagate(False)
        self._caja_lista.grid_columnconfigure(0, weight=1)

        lista = self._caja_lista
        self._lista = lista

        self._entradas_grid = []
        for fila, (sigla, clave, nombre, desc, color) in enumerate(MODULOS):
            boton_mod = BotonModulo(lista, sigla, nombre, desc, color,
                                    (lambda c=clave: self.ir_a(c)))
            boton_mod.grid(row=fila, column=0, sticky="ew", pady=3)
            self._entradas_grid.append(boton_mod)
            self.botones_modulo[clave] = boton_mod

        # --- Pie: estado del router y RUTA DE LA LLAVE PRIVADA ---
        self._pie_barra = pie = ctk.CTkFrame(barra, fg_color=C_PANEL,
                                             corner_radius=10)
        pie.grid(row=2, column=0, sticky="ew", padx=10, pady=(10, 16))
        self._pie_interior = dentro = ctk.CTkFrame(pie, fg_color="transparent")
        dentro.pack(fill="x", padx=12, pady=8)
        dentro.grid_columnconfigure(1, weight=1)

        self.punto_estado = ctk.CTkFrame(dentro, width=10, height=10,
                                         corner_radius=5, fg_color=C_AVISO)
        self.punto_estado.grid(row=0, column=0, padx=(0, 10))
        self.punto_estado.grid_propagate(False)

        self.lbl_router = ctk.CTkLabel(dentro, text="Comprobando...",
                                       font=F_NORMAL, text_color=C_TEXTO,
                                       anchor="w")
        self.lbl_router.grid(row=0, column=1, sticky="ew")
        self.lbl_destino = ctk.CTkLabel(dentro, text=USUARIO + "@" + IP,
                                        font=F_PEQUENA, text_color=C_SUAVE,
                                        anchor="w")
        self.lbl_destino.grid(row=1, column=1, sticky="ew", pady=(1, 0))

        # La ruta de la llave privada, siempre a la vista: es el dato que
        # hace falta cuando la conexion falla.
        separador = ctk.CTkFrame(dentro, height=1, fg_color=C_BORDE)
        separador.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 8))

        ctk.CTkLabel(dentro, text="LLAVE PRIVADA", font=F_PEQUENA,
                     text_color=C_SUAVE, anchor="w"
                     ).grid(row=3, column=0, columnspan=2, sticky="ew")

        self.lbl_llave = ctk.CTkLabel(dentro, text=LLAVE, font=F_MONO_PEQUENA,
                                      text_color=C_TEXTO, anchor="w",
                                      justify="left", wraplength=206)
        self.lbl_llave.grid(row=4, column=0, columnspan=2, sticky="ew",
                            pady=(2, 0))

        self.lbl_llave_estado = ctk.CTkLabel(dentro, text="", font=F_PEQUENA,
                                             text_color=C_SUAVE, anchor="w")
        self.lbl_llave_estado.grid(row=5, column=0, columnspan=2, sticky="ew",
                                   pady=(3, 0))

        self.refrescar_llave()

        # Se le da explicitamente el hueco que queda entre la cabecera y el
        # pie, y se recalcula cada vez que la ventana cambia de tamano.
        self._caja_lista.bind("<Configure>", self._ajustar_alto_lista)
        self.after(200, self._ajustar_alto_lista)

    def _ajustar_alto_lista(self, _evento=None):
        """Reparte entre los ocho modulos el alto REAL del contenedor.

        Se pregunta cuanto mide el hueco que grid le dio al contenedor y se
        divide entre el numero de modulos, porque lleva grid_propagate(False).
        """
        cuantos = len(self.botones_modulo)
        if cuantos == 0:
            return

        try:
            libre = self._caja_lista.winfo_height()
        except Exception:
            return
        if libre < 60:
            return

        # Con poco sitio se recorta tambien la separacion entre entradas.
        separacion = 3 if libre / cuantos >= 44 else 2

        alto_entrada = int(libre / cuantos) - separacion * 2
        alto_entrada = max(20, min(BotonModulo.ALTO, alto_entrada))

        if alto_entrada == getattr(self, "_alto_entrada", None):
            return                      # ya esta como toca, no repintar
        self._alto_entrada = alto_entrada

        for boton_mod in self.botones_modulo.values():
            boton_mod.ajustar(alto_entrada)
            boton_mod.grid_configure(pady=separacion)

    def refrescar_llave(self):
        """Repinta la ruta de la llave privada y si el archivo existe.

        Se llama al arrancar y cada vez que se guarda la conexion, para que
        el pie no se quede mostrando una ruta vieja.
        """
        self.lbl_llave.configure(text=LLAVE)
        if os.path.isfile(LLAVE):
            permisos = permisos_llave()
            correcto = permisos in ("600", "400")
            self.lbl_llave_estado.configure(
                text="existe  -  permisos " + (permisos or "?") +
                     ("" if correcto else "  (deberia ser 600)"),
                text_color=C_OK if correcto else C_AVISO)
        else:
            self.lbl_llave_estado.configure(text="no existe todavia",
                                            text_color=C_ERROR)

    def _bucle_trabajador(self):
        """Saca tareas de la cola y las ejecuta, una detras de otra.

        Vive en su propio hilo; el resultado se devuelve al de la interfaz
        con programar(), porque Tk no admite que otro hilo toque los widgets.
        """
        while True:
            tarea = self.cola.get()
            if tarea is None:
                break
            funcion, al_terminar = tarea
            try:
                resultado = funcion()
            except Exception as e:
                resultado = ErrorTarea(type(e).__name__ + ": " + str(e))
            self.programar(lambda r=resultado, cb=al_terminar: cb(r))
            self.cola.task_done()

    def encolar(self, funcion, al_terminar):
        """Mete una tarea en la cola del hilo trabajador."""
        if self.vivo:
            self.cola.put((funcion, al_terminar))

    def programar(self, funcion):
        """after() a prueba de cierres.

        Si el usuario cerro la ventana mientras el router contestaba,
        after() lanza RuntimeError o TclError; se ignoran a proposito.
        """
        try:
            self.after(0, funcion)
        except Exception:
            pass

    def refrescar_cabecera_async(self):
        """Pregunta al router por su nombre y repinta el indicador de arriba."""
        def consulta():
            ok, _ = hay_conexion()
            nombre = ""
            if ok:
                nombre = limpiar(consultar(":put [/system identity get name]",
                                           "consulta.sh"))
            return ok, nombre

        def cuando(datos):
            if isinstance(datos, ErrorTarea):
                self._pintar_cabecera(False, "")
            else:
                self._pintar_cabecera(datos[0], datos[1])

        self.encolar(consulta, cuando)

    def _pintar_cabecera(self, ok, nombre):
        """Actualiza el pie de la barra lateral y la pantalla de inicio."""
        if ok:
            self.punto_estado.configure(fg_color=C_OK)
            self.lbl_router.configure(text=nombre or "Router conectado",
                                      text_color=C_TEXTO)
        else:
            self.punto_estado.configure(fg_color=C_ERROR)
            self.lbl_router.configure(text="Sin conexion", text_color=C_ERROR)

        self.lbl_destino.configure(text=USUARIO + "@" + IP)
        self.refrescar_llave()

        inicio = self.paginas.get("inicio")
        if inicio is not None:
            inicio.pintar_estado(ok, nombre)

    def _ocultar_actual(self):
        if self.pagina_actual is not None:
            if hasattr(self.pagina_actual, "al_salir"):
                self.pagina_actual.al_salir()
            self.pagina_actual.grid_forget()

    def ir_a(self, clave):
        pagina = self.paginas.get(clave)
        if pagina is None:
            return
        self._ocultar_actual()
        pagina.grid(row=0, column=0, sticky="nsew")
        self.pagina_actual = pagina

        # Resalta el modulo que se esta viendo. "inicio" no tiene entrada
        # propia, asi que en ese caso no queda ninguno marcado.
        for c, boton_mod in self.botones_modulo.items():
            boton_mod.marcar(c == clave)

        pagina.al_entrar()

    def ir_al_menu(self):
        """Se conserva por compatibilidad: ahora lleva a la pantalla de inicio."""
        self.ir_a("inicio")

    # --- Cierre --------------------------------------------------------------

    def al_cerrar(self):
        """Se detienen SIEMPRE los scripts de monitoreo antes de salir.

        Sin esto quedarian cuatro procesos de bash en segundo plano despues
        de cerrar la ventana, haciendo ping y ssh para nadie.
        """
        self.vivo = False
        self.cola.put(None)          # despierta al hilo trabajador para que salga

        # Se avisa a la pagina activa para que cancele sus avisos pendientes,
        # o Tcl intenta ejecutar callbacks de widgets que ya no existen.
        if self.pagina_actual is not None and hasattr(self.pagina_actual, "al_salir"):
            try:
                self.pagina_actual.al_salir()
            except Exception:
                pass

        detener_monitoreo()
        self.destroy()


def main():
    escribir_scripts_monitoreo()
    Aplicacion().mainloop()


if __name__ == "__main__":
    main()
