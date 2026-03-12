# 📖 Setup Guide - Paso a Paso

Esta guía te llevará paso a paso para configurar el generador de test cases.

## ✅ Checklist Rápido

Antes de empezar, asegúrate de tener:
- [ ] Python instalado (versión 3.9+)
- [ ] Cuenta de Jira con acceso al proyecto
- [ ] Cuenta de Claude AI (plan de $20/mes)
- [ ] Cuenta de Google Cloud (gratuita)
- [ ] Cuenta de GitHub

---

## 📥 Paso 1: Instalar Python

### Windows:
1. Ve a https://www.python.org/downloads/
2. Descarga Python 3.11 o superior
3. **IMPORTANTE:** Durante instalación, marca "Add Python to PATH"
4. Verifica instalación:
   ```cmd
   python --version
   ```

### Mac:
1. Abre Terminal
2. Instala Homebrew (si no lo tienes):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
3. Instala Python:
   ```bash
   brew install python
   ```
4. Verifica:
   ```bash
   python3 --version
   ```

### Linux:
```bash
sudo apt update
sudo apt install python3 python3-pip
python3 --version
```

---

## 🔑 Paso 2: Obtener Token de Jira

1. Ve a: https://id.atlassian.com/manage-profile/security/api-tokens
2. Inicia sesión con tu cuenta de Atlassian
3. Click en **"Create API token"**
4. Nombre sugerido: `Test Case Generator`
5. **Copia el token** - guárdalo en un lugar seguro (no lo podrás ver de nuevo)

📝 **Guarda este token** - lo necesitarás en el Paso 6

---

## 🤖 Paso 3: Obtener API Key de Claude

1. Ve a: https://console.anthropic.com/
2. Inicia sesión o crea cuenta
3. En el menú lateral, click en **"API Keys"**
4. Click en **"Create Key"**
5. Dale un nombre: `Jira Test Generator`
6. **Copia la key** - guárdala en lugar seguro

📝 **Guarda esta key** - la necesitarás en el Paso 6

**Costo aproximado:** Con tu plan de $20/mes puedes generar ~500-1000 test cases/mes

---

## ☁️ Paso 4: Configurar Google Cloud (15 minutos)

### 4.1: Crear Proyecto

1. Ve a: https://console.cloud.google.com/
2. Inicia sesión con tu cuenta de Google
3. Click en el selector de proyectos (arriba)
4. Click **"NEW PROJECT"**
5. Nombre: `Jira Test Generator`
6. Click **"Create"**

### 4.2: Habilitar APIs

1. En el menú lateral, ve a **"APIs & Services" > "Library"**
2. Busca **"Google Drive API"**
3. Click en el resultado y luego **"ENABLE"**
4. Regresa a Library
5. Busca **"Google Docs API"**
6. Click y **"ENABLE"**

### 4.3: Crear Credenciales OAuth

1. Ve a **"APIs & Services" > "Credentials"**
2. Click **"+ CREATE CREDENTIALS"**
3. Selecciona **"OAuth client ID"**
4. Si te pide configurar "OAuth consent screen":
   - User Type: **External**
   - App name: `Jira Test Generator`
   - User support email: tu email
   - Developer contact: tu email
   - Click **Save and Continue** hasta terminar
5. Regresa a crear OAuth client ID:
   - Application type: **Desktop app**
   - Name: `Test Generator Desktop`
   - Click **CREATE**
6. **Descarga el JSON**
7. Renombra el archivo a `credentials.json`

📝 **Guarda este archivo** - lo necesitarás en el Paso 6

---

## 📁 Paso 5: Obtener ID de Carpeta de Google Drive

1. Abre Google Drive: https://drive.google.com/
2. Ve a la carpeta donde quieres guardar los test cases
3. Mira la URL en tu navegador:
   ```
   https://drive.google.com/drive/folders/1MazY7ZEo6_WUIunO7TJ4e2ZtAqT7UX9y
                                           ↑
                                    Este es el ID de la carpeta
   ```
4. **Copia solo la parte después de `/folders/`**

📝 **Guarda este ID** - lo necesitarás en el Paso 6

---

## 💻 Paso 6: Descargar y Configurar el Proyecto

### 6.1: Clonar desde GitHub

Opción A - Con Git instalado:
```bash
git clone https://github.com/TU-USUARIO/jira-test-case-generator.git
cd jira-test-case-generator
```

Opción B - Sin Git:
1. Ve al repositorio en GitHub
2. Click en **"Code" > "Download ZIP"**
3. Extrae el ZIP
4. Abre terminal/cmd en esa carpeta

### 6.2: Instalar Dependencias

```bash
pip install -r requirements.txt
```

Si da error, intenta:
```bash
pip3 install -r requirements.txt
```

### 6.3: Colocar credentials.json

1. Copia el archivo `credentials.json` que descargaste en el Paso 4
2. Pégalo en la carpeta del proyecto `jira-test-case-generator/`

### 6.4: Configurar .env

1. Copia el archivo de ejemplo:
   ```bash
   cp .env.example .env
   ```
   
   En Windows:
   ```cmd
   copy .env.example .env
   ```

2. Abre `.env` con un editor de texto (Notepad, VS Code, etc.)

3. Llena los valores con la información que guardaste:

```
JIRA_URL=https://fpsinc.atlassian.net
JIRA_EMAIL=tu-email@empresa.com
JIRA_API_TOKEN=el_token_que_copiaste_en_paso_2
JIRA_PROJECT=CCAI
CLAUDE_API_KEY=la_key_que_copiaste_en_paso_3
GOOGLE_DRIVE_FOLDER_ID=el_id_que_copiaste_en_paso_5
```

4. **Guarda el archivo**

---

## 🎯 Paso 7: Primera Ejecución

### 7.1: Ejecutar el Script

```bash
python generate_test_cases.py
```

En Mac/Linux:
```bash
python3 generate_test_cases.py
```

### 7.2: Autorizar Google

1. Se abrirá tu navegador automáticamente
2. Selecciona tu cuenta de Google
3. Click en **"Permitir"** cuando pida acceso a Drive y Docs
4. Verás un mensaje "The authentication flow has completed"
5. Cierra el navegador y regresa a la terminal

### 7.3: Ver Resultados

El script mostrará:
```
============================================================
🚀 JIRA TEST CASE GENERATOR
============================================================

🔍 Searching for assigned tickets...
✅ Found 3 ticket(s)

📋 Processing CCAI-490...
🤖 Generating test cases for CCAI-490...
✅ Test cases generated for CCAI-490
📄 Creating Google Doc for CCAI-490...
✅ Google Doc created: https://docs.google.com/...
💬 Adding comment to CCAI-490...
✅ Comment added to CCAI-490
✅ CCAI-490 completed!

============================================================
✅ Processed: 3
⏭️  Skipped: 0
📊 Total: 3
============================================================
```

---

## 🎉 ¡Listo!

Ahora cada vez que quieras generar test cases:

1. Abre terminal en la carpeta del proyecto
2. Ejecuta: `python generate_test_cases.py`
3. ¡Espera a que termine!

---

## 🐛 Problemas Comunes

### "python: command not found"
- Usa `python3` en vez de `python`
- Reinstala Python y marca "Add to PATH"

### "pip: command not found"
- Usa `pip3` en vez de `pip`
- Reinstala Python

### "No module named 'anthropic'"
```bash
pip install -r requirements.txt --force-reinstall
```

### "Invalid Jira credentials"
- Verifica que tu email sea correcto
- Regenera el API token de Jira
- Asegúrate de no tener espacios extra en el .env

### "Google authentication failed"
- Borra el archivo `token.json`
- Ejecuta de nuevo
- Asegúrate de haber habilitado las APIs en Google Cloud

### El script no encuentra tickets
- Verifica que tienes tickets asignados a ti
- Verifica que estén en estado "Assigned"
- Revisa que el nombre del proyecto sea correcto en `.env`

---

## 📞 ¿Necesitas Ayuda?

1. Revisa esta guía de nuevo
2. Busca el error en Google
3. Abre un Issue en GitHub con:
   - Sistema operativo
   - Versión de Python
   - Mensaje de error completo
   - Paso donde te atascaste

---

**¡Buena suerte! 🚀**
