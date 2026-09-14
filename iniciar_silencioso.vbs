' Lanza servidor.py (Luna) sin mostrar ninguna ventana de consola.
' Pensado para ponerlo como acceso directo en la carpeta de Inicio de Windows
' (shell:startup) y que Luna arranque sola al prender el computador.
'
' Cómo probarlo a mano: doble clic sobre este archivo. No debería abrirse
' ninguna ventana negra; a los pocos segundos se abre el navegador con el
' dashboard, igual que corriendo "python servidor.py" a mano.

Set objFSO = CreateObject("Scripting.FileSystemObject")
carpeta = objFSO.GetParentFolderName(WScript.ScriptFullName)

Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = carpeta

' pythonw.exe es como python.exe pero sin ventana de consola.
objShell.Run "pythonw " & Chr(34) & carpeta & "\servidor.py" & Chr(34), 0, False
