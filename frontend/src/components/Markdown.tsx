import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

// 渲染 assistant 的 Markdown（含表格）。内容来自自家 LLM，内部工具场景可接受。
export function Markdown({ text }: { text: string }) {
  const html = marked.parse(text || "") as string;
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}
