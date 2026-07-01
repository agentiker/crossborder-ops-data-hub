import {
  Calendar,
  LayoutDashboard,
  LogOut,
  MessagesSquare,
  PanelLeft,
  PanelLeftOpen,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  UserRound,
  Zap,
} from "lucide-react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { api, type ConversationItem, type Me } from "@/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const LOGOUT_PATH = "/board/auth/feishu/logout";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Plus;
  end?: boolean;
  bossOnly?: boolean;
  badge?: string; // 角标，如「待开发」
}

const NAV: NavItem[] = [
  { to: "/", label: "新建对话", icon: Plus, end: true },
  { to: "/scheduled", label: "定时任务", icon: Calendar, badge: "待开发" },
  { to: "/skills", label: "技能", icon: Zap, badge: "待开发" },
  { to: "/board", label: "看板", icon: LayoutDashboard },
  { to: "/admin", label: "管理", icon: ShieldCheck, bossOnly: true },
  { to: "/settings", label: "阈值配置", icon: SlidersHorizontal, bossOnly: true },
];

interface Props {
  me: Me | null;
  conversations: ConversationItem[];
  onRefresh: () => void;
  onNavigate?: () => void; // 移动端点击后关抽屉
  collapsed?: boolean; // 桌面收起态：仅留图标
  onToggleCollapse?: () => void; // 顶部 toggle（仅桌面渲染）
}

// StoreClaw 式左固定侧栏：wordmark + 导航 + 最近对话 + 底部账户。
// 在 AppShell 内（桌面固定列 / 移动抽屉）各渲染一次，宽度由外层给。
// collapsed=true（仅桌面）：隐藏文字/最近对话，导航收成居中图标 + title 悬浮提示。
export function SidebarContent({
  me,
  conversations,
  onRefresh,
  onNavigate,
  collapsed = false,
  onToggleCollapse,
}: Props) {
  const navigate = useNavigate();
  const { id } = useParams();
  const activeConvId = id ? Number(id) : null;

  const items = NAV.filter((n) => !n.bossOnly || me?.is_boss);

  async function onDelete(cid: number) {
    if (!confirm("删除该会话？")) return;
    await api.remove(cid);
    if (cid === activeConvId) navigate("/");
    onRefresh();
  }

  const navCls = ({ isActive }: { isActive: boolean }) =>
    cn(
      "flex h-9 items-center rounded-md text-sm transition-colors",
      collapsed ? "justify-center px-0" : "gap-2.5 px-2.5",
      isActive
        ? "bg-fill font-medium text-foreground"
        : "text-foreground-secondary hover:bg-fill hover:text-foreground",
    );

  return (
    <div className="flex h-full flex-col">
      {/* 顶栏：wordmark（展开时）+ 收起/展开 toggle（仅桌面有 onToggleCollapse 时渲染） */}
      <div
        className={cn(
          "flex h-14 shrink-0 items-center",
          collapsed ? "justify-center px-2" : "justify-between px-4",
        )}
      >
        {!collapsed && (
          <span className="font-display text-lg font-semibold tracking-tight">数据中枢</span>
        )}
        {onToggleCollapse && (
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            title={collapsed ? "展开侧栏" : "收起侧栏"}
            className="flex size-8 items-center justify-center rounded-lg text-foreground-secondary transition-colors hover:bg-fill hover:text-foreground"
          >
            {collapsed ? <PanelLeftOpen className="size-[18px]" /> : <PanelLeft className="size-[18px]" />}
          </button>
        )}
      </div>

      {/* 导航 */}
      <nav className="flex flex-col gap-0.5 px-3">
        {items.map(({ to, label, icon: Icon, end, badge }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={navCls}
            onClick={onNavigate}
            title={collapsed ? label : undefined}
          >
            <Icon className="size-[18px] shrink-0" />
            {!collapsed && <span className="truncate">{label}</span>}
            {!collapsed && badge && (
              <span className="ml-auto shrink-0 rounded-full bg-caution/15 px-1.5 py-0.5 text-[10px] font-medium text-caution">
                {badge}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* 最近对话（收起态隐藏，仅留导航图标） */}
      <div className={cn("mt-4 min-h-0 flex-1 overflow-y-auto px-3 scrollbar-hide", collapsed && "hidden")}>
        <div className="px-2.5 pb-1.5 text-xs font-medium text-foreground-tertiary">最近对话</div>
        {conversations.length === 0 ? (
          <div className="px-2.5 py-1 text-xs text-foreground-tertiary">还没有会话</div>
        ) : (
          conversations.map((c) => (
            <NavLink
              key={c.id}
              to={`/c/${c.id}`}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  "group flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
                  isActive
                    ? "bg-fill text-foreground"
                    : "text-foreground-secondary hover:bg-fill hover:text-foreground",
                )
              }
            >
              <MessagesSquare className="size-4 shrink-0 text-foreground-tertiary" />
              <span className="flex-1 truncate">{c.title || "新会话"}</span>
              <button
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onDelete(c.id);
                }}
                className="shrink-0 text-foreground-tertiary opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                title="删除"
              >
                <Trash2 className="size-3.5" />
              </button>
            </NavLink>
          ))
        )}
      </div>

      {/* 收起态撑开（最近对话隐藏后由此 spacer 把账户压到底部） */}
      {collapsed && <div className="flex-1" />}

      {/* 底部账户区 */}
      <div className={cn("border-t border-border-shallow p-3", collapsed && "px-2")}>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              title={collapsed ? (me ? (me.is_boss ? "老板" : "运营") : "未登录") : undefined}
              className={cn(
                "h-9 w-full min-w-0 gap-2",
                collapsed ? "justify-center px-0" : "justify-start px-2",
              )}
            >
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-fill-deep">
                <UserRound className="size-4" />
              </span>
              {!collapsed && (
                <span className="truncate text-sm">
                  {me ? (me.is_boss ? "老板" : "运营") : "未登录"}
                </span>
              )}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            <DropdownMenuLabel>
              {me ? `${me.is_boss ? "老板" : "运营"} · ${me.role}` : "未登录"}
            </DropdownMenuLabel>
            {me && (
              <DropdownMenuItem disabled className="text-xs text-foreground-tertiary">
                范围：{me.scope_label}
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <a href={LOGOUT_PATH}>
                <LogOut className="size-4" /> 退出登录
              </a>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
