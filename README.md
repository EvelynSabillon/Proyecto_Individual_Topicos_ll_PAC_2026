# Control MikroTik Router

Administración de un router MikroTik (RouterOS) desde Python sobre Linux.

* **Estudiante:** Evelyn Andrea Sabillon Limas 20212000317
* **Asignatura:** Tópicos Especiales y Avanzados
* **Periodo:** II PAC 2026
* **URL Video:** 

---

## 1. Descripción del proyecto

Aplicación de escritorio que administra un router MikroTik por SSH, con
arquitectura **Frontend + Backend** separada en capas dentro de un único
archivo de Python.

* **Frontend** — interfaz gráfica hecha con CustomTkinter. Una sola ventana
  dividida en dos: una **barra lateral** fija con los ocho módulos, y a su
  derecha el panel de la página activa. El módulo que se está viendo queda
  resaltado en la barra. No se abre ninguna ventana emergente en todo el
  programa: los mensajes de éxito y de error salen en el panel **RESULTADO**,
  que aparece **debajo del formulario** solo cuando hay algo que mostrar y se
  pliega con el botón *Ocultar*.

  El pie de la barra lateral muestra siempre el estado del router y la **ruta
  de la llave privada**, con aviso si el archivo no existe o si sus permisos
  no son los correctos. Es el dato que hace falta justo cuando la conexión
  falla.

* **Backend** — capa de lógica en Python que valida los datos, escribe un
  **script `.sh`** con los parámetros del formulario ya dentro, lo ejecuta con
  `bash`, captura la respuesta del router y **verifica** que el cambio quedara
  aplicado de verdad antes de dar nada por bueno.

El camino que recorre cada orden es:

```
Frontend (CustomTkinter)
     │  el usuario llena el formulario y pulsa el botón
     ▼
Backend / operación op_*()        valida → ejecuta → VERIFICA
     │
     ▼
ejecutar("create_IP.sh", "ip address add ...")
     │  escribe el script en backend/ con los parámetros dentro
     ▼
bash backend/create_IP.sh
     │
     ▼
ssh -i <llave> <usuario>@<ip> 'comando de RouterOS'
     │
     ▼
RouterOS  →  la respuesta se captura y se interpreta
```

Los scripts de la carpeta `backend/` son el backend de
shell scripting, se regeneran en cada ejecución con los
datos que escribió el usuario y quedan en disco como evidencia auditable. Se
pueden abrir con cualquier editor y ejecutar a mano desde la terminal.

### Funcionalidades

| #  | Funcionalidad | Página | Script `.sh` que genera |
|----|-----------------------------------------|-----------------|----------------------------------------------------------------------------------------------------|
| 1  | Asignar el nombre (identity) del router | Identidad       | `routername.sh`                                                                                    |
| 2  | Crear una dirección IP                  | Direcciones IP  | `create_IP.sh`                                                                                     |
| 3  | Eliminar una dirección IP               | Direcciones IP  | `IPDelete.sh`                                                                                      |
| 4  | Crear un servidor DHCP                  | Servidor DHCP   | `create_ip_interface.sh`, `create_dhcp_pool.sh`, `create_dhcp_server.sh`, `create_dhcp_network.sh` |
| 5  | Eliminar un servidor DHCP               | Servidor DHCP   | `delete_dhcp_server.sh`, `delete_dhcp_pool.sh`, `delete_dhcp_network.sh`                           |
| 6  | Configurar servidores DNS               | Servidores DNS  | `configurar_dns.sh`                                                                                |
| 7  | Eliminar la configuración de DNS        | Servidores DNS  | `eliminar_dns.sh`                                                                                  |
| 8  | Crear rutas estáticas                   | Rutas estáticas | `route_add.sh`                                                                                     |
| 9  | Eliminar rutas estáticas                | Rutas estáticas | `route_remove.sh`                                                                                  |
| 10 | Monitorear dos interfaces en tiempo     | Monitoreo       | `verificarinterfaces.sh`, `monitorearinterfaces.sh`, `verificarconexion.sh`, `monitorearip.sh`     |
| 11 | Crear un respaldo (backup)              | Respaldos       | `respaldoMK.sh`                                                                                    |
| 12 | Listar todos los respaldos disponibles  | Respaldos       | consulta directa                                                                                   |

Además, como apoyo:

* **Conexión y llaves SSH** — cambia la IP, el usuario y la ruta de la llave
  sin tocar el código (se guarda en `conexion.ini`), y hace desde la propia
  aplicación los tres pasos de la autenticación por llave: generar el par,
  subir la llave pública al router e importarla en RouterOS.

---

## 2. Requisitos

### Máquinas

* Una máquina virtual con **Linux** (probado en Ubuntu/Debian).
* Una máquina virtual con **MikroTik RouterOS**, alcanzable por red desde la
  anterior (host-only de VirtualBox).

### Software en la máquina Linux

| Requisito             | Cómo se instala |
|----------------------------------------------|-----------------------------------|
| Python 3.8 o superior                        | ya viene en la distribución       |
| Tkinter                                      | `sudo apt install python3-tk`     |
| Cliente OpenSSH (`ssh`, `scp`, `ssh-keygen`) | `sudo apt install openssh-client` |
| `ping`                                       | `sudo apt install iputils-ping`   |
| CustomTkinter                                | `pip install customtkinter`       |
| Pillow (solo para los iconos del monitoreo)  | `pip install Pillow`              |

Tkinter **no** se instala con `pip`: viene con el sistema operativo.

### En el router

El servicio SSH tiene que estar habilitado:

```
/ip service enable ssh
```

---

## 3. Instalación

```bash
# 1. Copiar la carpeta del proyecto a la máquina Linux
cd ~/mikrotik

# 2. Instalar Tkinter y el cliente SSH (una sola vez)
sudo apt update
sudo apt install python3-tk openssh-client iputils-ping

# 3. Instalar la dependencia de Python
pip install -r requirements.txt
#   o directamente:  pip install customtkinter
```

> Si `pip` avisa de *externally-managed-environment*, usa
> `pip install --break-system-packages customtkinter` o crea un entorno
> virtual con `python3 -m venv venv && source venv/bin/activate`.

No hace falta `sudo` para ejecutar el programa, ni dar permisos especiales a
ninguna carpeta: los scripts se escriben en `backend/`, que está al lado del
archivo `.py` y pertenece al mismo usuario.

---

## 4. Ejecución

```bash
python3 mikrotik_system.py
```

### Primera vez: dejar lista la conexión

1. Abrir la página **Conexión y llaves SSH**.
2. Escribir la **IP del router**, el **usuario** (normalmente `admin`) y la
   ruta donde se quiere guardar la llave privada. Pulsar **Guardar**: queda
   en `conexion.ini` y ya no hay que volver a escribirlo.
3. Escribir la **contraseña del router** y pulsar
   **Instalar llave en el router**. Eso hace los tres pasos seguidos:

   | Paso                | Equivale a                                                              |
   |---------------------|-------------------------------------------------------------------------|
   | 1. Generar el par   | `ssh-keygen -t rsa -b 2048 -f ~/.ssh/mikrotik_tea_key -N ""`            |
   | 2. Subir la pública | `scp ~/.ssh/mikrotik_tea_key.pub admin@<ip>:/`                          |
   | 3. Importarla       | `/user ssh-keys import public-key-file=mikrotik_tea_key.pub user=admin` |

   La contraseña solo hace falta en los pasos 2 y 3, se usa en memoria, no se
   guarda en ningún archivo y no aparece en la línea de comandos (se responde
   por un terminal falso creado con el módulo `pty`, en vez de usar `sshpass`).

4. Pulsar **Probar conexión**. Si dice *«El router contestó sin pedir
   contraseña»*, todo lo demás ya funciona.

Si algo falla, el botón **Ver estado** explica en qué paso está el problema.
El aviso más frecuente es el de permisos: la llave privada tiene que estar en
`600` y hay un botón **Arreglar permisos** que lo corrige.

### Uso normal

La barra lateral tiene los ocho módulos y está siempre visible: se cambia de
uno a otro con un clic, sin pasar por ningún menú intermedio. Al pulsar sobre
el título *CONTROL MIKROTIK* se vuelve a la pantalla de inicio. En cada
página:

* El formulario ocupa el ancho del panel, repartido en dos columnas de
  tarjetas.
* El panel **RESULTADO** aparece debajo en cuanto hay algo que enseñar: punto
  verde si salió bien, rojo si no, y la respuesta literal del router. El
  botón *Ocultar* lo pliega y devuelve todo el alto al formulario.
* Los botones de borrado piden confirmación **sin abrir ninguna ventana**: el
  primer clic los pone en ámbar con el texto *«Pulsa otra vez para
  confirmar»* y se desarman solos a los cinco segundos.

En **Monitoreo en vivo** se eligen dos interfaces y se pulsa *Iniciar
monitoreo*. Los datos los recogen cuatro scripts de bash que corren en
segundo plano y escriben en `runtime/`; la ventana solo lee esos archivos una
vez por segundo, así que no se congela aunque el router tarde en responder.
Al salir de la página o cerrar el programa, los scripts se detienen solos.

Además del valor instantáneo que se ve en los paneles, el monitoreo deja un
**registro histórico fechado** en `runtime/trafico.log`, con una línea por
interfaz y por segundo:

```
===== monitoreo iniciado  2026-08-03 14:32:05  (ether1 y ether2) =====
2026-08-03 14:32:06  ether1     UP    IN 8.19 Mbps    OUT 2.11 Mbps
2026-08-03 14:32:06  ether2     DOWN  IN 0 bps        OUT 0 bps
2026-08-03 14:32:07  ether1     UP    IN 4.82 Mbps    OUT 1.23 Mbps
2026-08-03 14:32:07  ether2     DOWN  IN 0 bps        OUT 0 bps
```

Los semáforos de estado usan los iconos `imagenes/on.png` y
`imagenes/off.png`, tanto en los dos paneles de interfaz como en la fila del
ping. Están dibujados por código (`imagenes/generar_iconos.py`) en lugar de
retocados a mano: así el fondo queda transparente de verdad, los colores
salen exactos de la paleta y se pueden regenerar en cualquier tamaño. En
`imagenes/alternativos/` hay una segunda pareja con colores tomados
íntegramente de la paleta; para usarla basta con copiar esos dos archivos
sobre los de `imagenes/`. Para el estado *«esperando datos»* se genera al vuelo una
versión en escala de grises del icono verde, así que no hace falta un tercer
archivo. Si las imágenes no están, o si Pillow no está instalado, la
aplicación **no falla**: dibuja un círculo de color del mismo tamaño y sigue
funcionando igual.

El registro se ve en vivo en la parte baja de la página, y el botón *Abrir
registro completo* vuelca dentro de la misma ventana las últimas 500 líneas.
El archivo se recorta solo a esas 500 líneas para que no crezca sin control,
pero **no** se borra al reiniciar el monitoreo: cada sesión añade su propia
línea de separación, así que el historial de varias sesiones convive en el
mismo archivo.

---

## 5. Estructura de archivos

```
mikrotik_system.py     todo el programa (frontend + backend)
requirements.txt       dependencias de pip
conexion.ini           IP, usuario y ruta de la llave (lo escribe la app)
README.md              este archivo

backend/               scripts .sh generados por la aplicación
    routername.sh          asignar el nombre del router
    create_IP.sh           crear una dirección IP
    IPDelete.sh            eliminar una dirección IP
    create_ip_interface.sh  paso 0 del DHCP: IP en la interfaz
    create_dhcp_pool.sh     paso 1 del DHCP: pool
    create_dhcp_server.sh   paso 2 del DHCP: servidor
    create_dhcp_network.sh  paso 3 del DHCP: red y DNS
    delete_dhcp_server.sh   borrar el servidor DHCP
    delete_dhcp_pool.sh     borrar el pool
    delete_dhcp_network.sh  borrar la red
    configurar_dns.sh       configurar los DNS
    eliminar_dns.sh         quitar los DNS
    route_add.sh            crear ruta estática
    route_remove.sh         eliminar ruta estática
    respaldoMK.sh           crear el respaldo en el router
    consulta.sh             última consulta de solo lectura
    verificarconexion.sh    monitoreo ICMP: productor
    monitorearip.sh         monitoreo ICMP: consumidor
    verificarinterfaces.sh  monitoreo de interfaces: productor
    monitorearinterfaces.sh monitoreo de interfaces: consumidor

imagenes/              iconos del monitoreo
    on.png                 interfaz UP   (verde menta)
    off.png                interfaz DOWN (magenta)
    generar_iconos.py      script que los dibuja, por si quieres cambiarlos
    alternativos/          pareja con colores 100% de la paleta
        on.png                 azul claro
        off.png                magenta

runtime/               archivos del monitoreo
    estado.txt             1 = el router responde al ping, 0 = no
    estado1.txt/2.txt      1 = interfaz UP, 0 = DOWN   (valor instantáneo)
    trafico1.txt/2.txt     "<bits_in> <bits_out>"      (valor instantáneo)
    datosconexion.txt      historial del ping, con hora
    datosinterfaces.txt    última respuesta cruda del router
    trafico.log            REGISTRO fechado del tráfico (últimas 500 líneas)
Backups/               respaldos traídos del router al PC
```

Las carpetas `backend/`, `runtime/` y `Backups/` se crean solas si no existen.

---

## 6. Colores

La paleta es *candy scar*. El color no es decorativo, distingue lo que hace
cada botón:

| Uso                                                   | Color                 |
|-------------------------------------------------------|-----------------------|
| Fondo de la ventana                                   | `#17121B`             |
| Tarjetas y paneles                                    | `#1E2830` (derivado)  |
| Bordes y botones secundarios                          | `#2F4B59`             |
| Acción normal, sin riesgo (Guardar, Crear, Consultar) | `#3F7784`             |
| Destruir o error (Eliminar, mensajes de fallo)        | `#F9018B` / `#840238` |
| Texto principal                                       | `#FCFEFD`             |
| Texto secundario                                      | `#97C9EC`             |
| Procesando / esperando                                | `#F7C5FE`             |

Verde y rojo (`#3FD69A` y los iconos) se mantienen **fuera de la paleta** a
propósito: son los dos únicos colores que se leen sin pensar como «funciona»
y «no funciona», y el monitoreo depende de que eso se entienda de un vistazo.
El verde se eligió con el mismo tono frío que los turquesas para que no
desentone.

---
