import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { api, type RoleRow } from "@/api";
import { useMe } from "@/components/shell/AppShell";
import { DataTable, type Column } from "@/components/DataTable";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const COLUMNS: Column<RoleRow>[] = [
  { key: "open_id", header: "open_id", render: (r) => <span className="font-mono text-xs">{r.open_id}</span> },
  {
    key: "role",
    header: "角色",
    render: (r) => (
      <Badge variant={r.role === "boss" ? "default" : "secondary"}>
        {r.role === "boss" ? "老板" : "运营"}
      </Badge>
    ),
  },
  { key: "scope", header: "范围上限", render: (r) => (r.role === "boss" ? "全部" : r.allowed_scope_key || "—") },
  { key: "note", header: "备注", render: (r) => r.note || "—" },
  {
    key: "is_active",
    header: "状态",
    render: (r) =>
      r.is_active ? (
        <Badge variant="success">启用</Badge>
      ) : (
        <Badge variant="outline">停用</Badge>
      ),
  },
];

export function AdminPage() {
  const me = useMe();
  const [rows, setRows] = useState<RoleRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (me && !me.is_boss) return; // 非 boss 不请求
    api
      .adminRoles()
      .then((r) => setRows(r.items))
      .catch((e) => setError(String(e)));
  }, [me]);

  // me 尚未加载：占位
  if (me === null) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-6">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="mt-6 h-48 w-full" />
      </div>
    );
  }

  // 前端软隔离：非 boss 回对话页（硬隔离仍在后端 require_boss）。
  if (!me.is_boss) return <Navigate to="/" replace />;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
        <PageHeader title="用户权限管理" scope="user_roles 真相源" />

        <Card className="mt-3 border-dashed">
          <CardContent className="py-4 text-center text-sm text-muted-foreground">
            当前为只读视图。新增 / 改角色 / 改范围 / 停用将在 Phase C 接入（后端 boss-only API 已就绪）。
          </CardContent>
        </Card>

        <div className="mt-3">
          {error ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-destructive">
                加载失败：{error}
              </CardContent>
            </Card>
          ) : (
            <DataTable
              columns={COLUMNS}
              rows={rows ?? []}
              rowKey={(r) => `${r.channel}/${r.account_id}/${r.open_id}`}
              empty={rows === null ? "加载中…" : "暂无用户角色"}
            />
          )}
        </div>
      </div>
    </div>
  );
}
