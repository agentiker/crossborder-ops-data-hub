import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { Ban, Check, MoreHorizontal, Pencil, UserPlus } from "lucide-react";
import {
  api,
  type AdminScopeOption,
  type RoleRow,
  type RoleUpsertBody,
} from "@/api";
import { useMe } from "@/components/shell/AppShell";
import { DataTable, type Column } from "@/components/DataTable";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type FormMode =
  | { kind: "create" }
  | { kind: "edit"; row: RoleRow }
  | { kind: "approve"; row: RoleRow }; // 通过自助申请：预填 open_id、强制选范围后开通

export function AdminPage() {
  const me = useMe();
  const [rows, setRows] = useState<RoleRow[] | null>(null);
  const [scopes, setScopes] = useState<AdminScopeOption[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<FormMode | null>(null);
  const [confirmRow, setConfirmRow] = useState<RoleRow | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);

  useEffect(() => {
    if (me && !me.is_boss) return;
    if (!me) return;
    Promise.all([api.adminRoles(), api.adminScopes()])
      .then(([r, s]) => {
        setRows(r.items);
        setScopes(s.items);
      })
      .catch((e) => setLoadError(String(e instanceof Error ? e.message : e)));
  }, [me]);

  // 待审批（自助申请）置顶、按申请时间升序，其余保持原序；让老板一眼看到待办。
  const sortedRows = useMemo(() => {
    if (!rows) return rows;
    const pending = rows
      .filter(isPending)
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
    const rest = rows.filter((r) => !isPending(r));
    return [...pending, ...rest];
  }, [rows]);
  const pendingCount = (rows ?? []).filter(isPending).length;

  // 行高亮 2s 自动褪。
  useEffect(() => {
    if (!flashId) return;
    const t = setTimeout(() => setFlashId(null), 2000);
    return () => clearTimeout(t);
  }, [flashId]);

  const onSaved = (saved: RoleRow) => {
    setRows((prev) => upsertRow(prev ?? [], saved));
    setFlashId(rowKey(saved));
    setForm(null);
  };

  const onDeactivated = (deact: RoleRow) => {
    setRows((prev) => upsertRow(prev ?? [], deact));
    setFlashId(rowKey(deact));
    setConfirmRow(null);
  };

  const columns = useMemo<Column<RoleRow>[]>(() => {
    const myOpenId = me?.open_id ?? "";
    return [
      {
        key: "open_id",
        header: "open_id",
        render: (r) => (
          <span className="font-mono text-xs" title={r.open_id}>
            {r.open_id}
          </span>
        ),
      },
      {
        key: "role",
        header: "角色",
        render: (r) =>
          isPending(r) ? (
            <span className="text-muted-foreground">待定</span>
          ) : (
            <Badge variant={r.role === "boss" ? "default" : "secondary"}>
              {r.role === "boss" ? "老板" : "运营"}
            </Badge>
          ),
      },
      {
        key: "scope",
        header: "范围上限",
        render: (r) => {
          if (r.role === "boss") return <span className="text-muted-foreground">全部</span>;
          if (!r.allowed_scope_key) return <span className="text-muted-foreground">—</span>;
          const name =
            scopes.find((s) => s.scope_key === r.allowed_scope_key)?.scope_name ||
            r.allowed_scope_key;
          return (
            <span title={r.allowed_scope_key}>
              {name}
            </span>
          );
        },
      },
      { key: "note", header: "备注", render: (r) => r.note || <span className="text-muted-foreground">—</span> },
      {
        key: "is_active",
        header: "状态",
        render: (r) =>
          isPending(r) ? (
            <Badge variant="warning">待审批</Badge>
          ) : r.is_active ? (
            <Badge variant="success">启用</Badge>
          ) : (
            <Badge variant="outline">停用</Badge>
          ),
      },
      {
        key: "actions",
        header: "",
        render: (r) => {
          const isSelf = r.open_id === myOpenId;
          if (isPending(r)) {
            // 待审批：直给「通过」主操作（开预填表单选范围），不藏在下拉里。
            return (
              <Button size="sm" onClick={() => setForm({ kind: "approve", row: r })}>
                <Check className="size-3.5" /> 通过
              </Button>
            );
          }
          return (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  aria-label="操作"
                >
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => setForm({ kind: "edit", row: r })}>
                  <Pencil className="size-3.5" /> 编辑
                </DropdownMenuItem>
                <DropdownMenuItem
                  className={cn(
                    "text-destructive focus:text-destructive",
                    (isSelf || !r.is_active) && "pointer-events-none opacity-50",
                  )}
                  onSelect={() => {
                    if (isSelf || !r.is_active) return;
                    setConfirmRow(r);
                  }}
                  title={isSelf ? "不能停用自己" : !r.is_active ? "已停用" : ""}
                >
                  <Ban className="size-3.5" /> 停用
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          );
        },
      },
    ];
  }, [scopes, me?.open_id]);

  if (me === null) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-6">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="mt-6 h-48 w-full" />
      </div>
    );
  }
  if (!me.is_boss) return <Navigate to="/" replace />;

  return (
    <div className="flex-1">
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
        <PageHeader
          title="用户权限管理"
          scope={pendingCount > 0 ? `${pendingCount} 个待审批申请` : "user_roles 真相源"}
          actions={
            <Button size="sm" onClick={() => setForm({ kind: "create" })}>
              <UserPlus className="size-4" /> 新增成员
            </Button>
          }
        />

        <div className="mt-5">
          {loadError ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-destructive">
                加载失败：{loadError}
              </CardContent>
            </Card>
          ) : (
            <DataTable
              columns={columns}
              rows={sortedRows ?? []}
              rowKey={rowKey}
              empty={rows === null ? "加载中…" : "暂无用户角色"}
              className={cn(flashId && "ring-1 ring-transparent")}
              rowClassName={(r) =>
                rowKey(r) === flashId ? "bg-accent/60 transition-colors" : ""
              }
            />
          )}
        </div>
      </div>

      {form && (
        <RoleFormDialog
          mode={form}
          scopes={scopes}
          onClose={() => setForm(null)}
          onSaved={onSaved}
        />
      )}
      {confirmRow && (
        <DeactivateConfirm
          row={confirmRow}
          onClose={() => setConfirmRow(null)}
          onDone={onDeactivated}
        />
      )}
    </div>
  );
}

// ── helpers ─────────────────────────────────────────────────────────────────

const rowKey = (r: RoleRow) => `${r.channel}/${r.account_id}/${r.open_id}`;

// 待审批 = 自助申请落库的哨兵：role=pending 且未启用（OAuth 回调 ensure_registration 写入）。
const isPending = (r: RoleRow) => r.role === "pending" && !r.is_active;

function upsertRow(rows: RoleRow[], saved: RoleRow): RoleRow[] {
  const key = rowKey(saved);
  const idx = rows.findIndex((r) => rowKey(r) === key);
  if (idx === -1) return [saved, ...rows];
  const copy = rows.slice();
  copy[idx] = saved;
  return copy;
}

// ── 表单弹窗 ────────────────────────────────────────────────────────────────

function RoleFormDialog({
  mode,
  scopes,
  onClose,
  onSaved,
}: {
  mode: FormMode;
  scopes: AdminScopeOption[];
  onClose: () => void;
  onSaved: (r: RoleRow) => void;
}) {
  const isExisting = mode.kind === "edit" || mode.kind === "approve";
  const initial = isExisting ? mode.row : null;
  const [openId, setOpenId] = useState(initial?.open_id ?? "");
  // 待审批行 role=pending 不是合法选项，开通时默认 operator；编辑老板/运营则沿用原角色。
  const [role, setRole] = useState<"boss" | "operator">(
    initial?.role === "boss" ? "boss" : "operator",
  );
  const [scopeKey, setScopeKey] = useState(initial?.allowed_scope_key ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const me = useMe();
  const isSelfEdit = mode.kind === "edit" && mode.row.open_id === me?.open_id;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const oid = openId.trim();
    if (!oid) {
      setError("open_id 不能为空");
      return;
    }
    if (role === "operator" && !scopeKey) {
      setError("operator 必须选择数据范围");
      return;
    }
    if (isSelfEdit && role === "operator") {
      setError("不能把自己改成 operator（会被自己锁出）");
      return;
    }
    setSubmitting(true);
    const body: RoleUpsertBody = {
      open_id: oid,
      role,
      scope_key: role === "operator" ? scopeKey : null,
      note: note.trim() || null,
    };
    try {
      const saved = await api.adminUpsertRole(body);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && !submitting && onClose()}>
      <DialogContent onPointerDownOutside={(e) => submitting && e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>
            {mode.kind === "create"
              ? "新增成员"
              : mode.kind === "approve"
                ? "通过申请并开通权限"
                : "编辑成员"}
          </DialogTitle>
          <DialogDescription>
            {mode.kind === "approve"
              ? "为该申请人选定角色与数据范围，保存即开通；TA 刷新页面即可访问。"
              : mode.kind === "create"
                ? "boss 看全部范围；operator 必须绑定一个数据范围作为不可越界的硬上限。"
                : "修改后立即生效；停用成员请用列表行的「停用」操作。"}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="open_id">open_id</Label>
            <Input
              id="open_id"
              value={openId}
              onChange={(e) => setOpenId(e.target.value)}
              placeholder="ou_xxxxxxxxxxxxxxxxxxxxxxxxxx"
              readOnly={isExisting}
              required
              autoFocus={mode.kind === "create"}
              className="font-mono text-sm"
            />
            <p className="text-xs text-muted-foreground">
              飞书 open_id。通常无需手填——成员自己登录后会自动出现在上方「待审批」列表，点该行「通过」即可。
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="role">角色</Label>
              <SelectNative
                id="role"
                value={role}
                onChange={(v) => setRole(v as "boss" | "operator")}
              >
                <option value="operator">运营 operator</option>
                <option value="boss">老板 boss</option>
              </SelectNative>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="scope">数据范围</Label>
              <SelectNative
                id="scope"
                value={scopeKey}
                onChange={setScopeKey}
                disabled={role === "boss"}
              >
                <option value="">{role === "boss" ? "—（boss 看全部）" : "请选择…"}</option>
                {scopes.map((s) => (
                  <option key={s.scope_key} value={s.scope_key}>
                    {s.scope_name}
                  </option>
                ))}
              </SelectNative>
            </div>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="note">备注</Label>
            <Input
              id="note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="可选，便于辨认是谁"
            />
          </div>

          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              取消
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── 停用确认 ────────────────────────────────────────────────────────────────

function DeactivateConfirm({
  row,
  onClose,
  onDone,
}: {
  row: RoleRow;
  onClose: () => void;
  onDone: (r: RoleRow) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const r = await api.adminDeactivateRole(row.open_id, row.account_id, row.channel);
      onDone(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && !submitting && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>停用该成员？</DialogTitle>
          <DialogDescription>
            停用后该成员将立即失去访问权限，但记录保留可日后恢复。
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-md border bg-muted/40 px-3 py-2 text-xs">
          <div className="font-mono">{row.open_id}</div>
          {row.note && <div className="mt-0.5 text-muted-foreground">{row.note}</div>}
        </div>
        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button type="button" variant="destructive" onClick={submit} disabled={submitting}>
            {submitting ? "停用中…" : "确认停用"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── 原生 select，套与 Input 同款样式（项目无 Select primitive 也不引）─────

function SelectNative({
  id,
  value,
  onChange,
  disabled,
  children,
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={cn(
        "flex h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
      )}
    >
      {children}
    </select>
  );
}
