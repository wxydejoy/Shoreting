' Silent Windows probe: no console window.
Set sh = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
pyw = root & "\.venv\Scripts\pythonw.exe"
sh.Run """" & pyw & """ -m dock_hub --probe", 0, False
