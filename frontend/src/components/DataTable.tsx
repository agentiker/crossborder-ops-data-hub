import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// 统一表格壳（Admin/Board 复用）。数字列右对齐 + 等宽数字。
export interface Column<T> {
  key: string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
  align?: "left" | "right";
  numeric?: boolean;
  className?: string;
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string | number;
  empty?: ReactNode;
  className?: string;
}

export function DataTable<T>({ columns, rows, rowKey, empty = "暂无数据", className }: Props<T>) {
  return (
    <div className={cn("overflow-hidden rounded-lg border", className)}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            {columns.map((c) => (
              <th
                key={c.key}
                className={cn(
                  "px-3 py-2.5 text-xs font-medium uppercase tracking-wide text-muted-foreground",
                  c.align === "right" || c.numeric ? "text-right" : "text-left",
                  c.className,
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-3 py-10 text-center text-sm text-muted-foreground"
              >
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={rowKey(row, i)} className="border-b last:border-0 hover:bg-muted/40">
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      "px-3 py-2.5",
                      c.align === "right" || c.numeric ? "text-right" : "text-left",
                      c.numeric && "tabnum",
                      c.className,
                    )}
                  >
                    {c.render ? c.render(row) : (row as Record<string, ReactNode>)[c.key]}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
