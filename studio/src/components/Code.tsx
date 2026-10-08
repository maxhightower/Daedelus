import Editor, { DiffEditor, loader } from "@monaco-editor/react";
// Core editor + Monarch syntax highlighting only (no language-service workers needed).
import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution";
import "monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution";
import "monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution";
import "monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

// Bundle Monaco locally (no CDN) so the desktop app works offline.
(self as any).MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });

const lang = (path: string) => {
  const ext = path.split(".").pop() ?? "";
  return { py: "python", json: "javascript", ts: "typescript", tsx: "typescript", js: "javascript", md: "markdown", diff: "diff" }[ext] ?? "plaintext";
};

export function CodeView({ path, text, height = 360 }: { path: string; text: string; height?: number }) {
  return (
    <Editor
      height={height}
      theme="vs-dark"
      path={path}
      language={lang(path)}
      value={text}
      options={{ readOnly: true, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false }}
    />
  );
}

export function CodeDiff({ path, before, after, height = 360 }: { path: string; before: string; after: string; height?: number }) {
  return (
    <DiffEditor
      height={height}
      theme="vs-dark"
      language={lang(path)}
      original={before}
      modified={after}
      keepCurrentOriginalModel
      keepCurrentModifiedModel
      options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12 }}
    />
  );
}
