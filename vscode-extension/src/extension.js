// Cliente LSP para Compiscript: arranca `program/lsp_server.py` como
// proceso hijo y lo conecta a VS Code por stdio. Los diagnosticos llegan
// solos via el protocolo LSP; los comandos custom piden el arbol
// sintactico (para pintarlo en un webview) y el codigo intermedio.

const path = require("path");
const vscode = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");
const { renderParseTreePanel } = require("./parseTreePanel");

let client;

function resolveSetting(value, workspaceFolder) {
  return value.replace("${workspaceFolder}", workspaceFolder ? workspaceFolder.uri.fsPath : "");
}

function activate(context) {
  const config = vscode.workspace.getConfiguration("compiscript");
  const workspaceFolder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];

  const pythonPath = config.get("pythonPath", "python3");
  const serverScript = resolveSetting(config.get("serverScript"), workspaceFolder);

  const serverOptions = {
    command: pythonPath,
    args: [serverScript],
    transport: TransportKind.stdio,
  };

  const clientOptions = {
    documentSelector: [{ scheme: "file", language: "compiscript" }],
  };

  client = new LanguageClient(
    "compiscriptLanguageServer",
    "Compiscript Language Server",
    serverOptions,
    clientOptions
  );

  context.subscriptions.push(client.start());

  context.subscriptions.push(
    vscode.commands.registerCommand("compiscript.restartServer", async () => {
      await client.stop();
      context.subscriptions.push(client.start());
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("compiscript.showParseTree", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.languageId !== "compiscript") {
        vscode.window.showWarningMessage("Abre un archivo .cps para ver su arbol sintactico.");
        return;
      }

      const uri = editor.document.uri.toString();
      try {
        const tree = await client.sendRequest("workspace/executeCommand", {
          command: "compiscript/parseTree",
          arguments: [uri],
        });
        if (tree && tree.error) {
          vscode.window.showWarningMessage(tree.error);
          return;
        }
        renderParseTreePanel(context, path.basename(editor.document.fileName), tree);
      } catch (err) {
        vscode.window.showErrorMessage(`No se pudo obtener el arbol sintactico: ${err.message || err}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("compiscript.showTac", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.languageId !== "compiscript") {
        vscode.window.showWarningMessage("Abre un archivo .cps para compilarlo.");
        return;
      }

      try {
        const result = await client.sendRequest("workspace/executeCommand", {
          command: "compiscript/tac",
          arguments: [editor.document.uri.toString()],
        });
        if (result.error) {
          vscode.window.showWarningMessage(result.error);
          return;
        }
        if (result.errors) {
          vscode.window.showErrorMessage(
            `El programa tiene ${result.errors.length} error(es); corrigelos para generar el TAC.`
          );
          return;
        }

        const name = path.basename(editor.document.fileName);
        const symbols = result.symbols.split("\n").map((line) => `; ${line}`).join("\n");
        const content = [
          `; Codigo intermedio (TAC) de ${name}`,
          "",
          result.tac,
          "",
          "; Tabla de simbolos con direcciones y registros de activacion",
          symbols,
          "",
        ].join("\n");
        const doc = await vscode.workspace.openTextDocument({ content, language: "plaintext" });
        await vscode.window.showTextDocument(doc, vscode.ViewColumn.Beside);
      } catch (err) {
        vscode.window.showErrorMessage(`No se pudo generar el codigo intermedio: ${err.message || err}`);
      }
    })
  );
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
