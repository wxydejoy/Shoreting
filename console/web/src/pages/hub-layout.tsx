import { PageHeader } from "@/components/page";
import { PhoneLayoutEditor } from "@/pages/hub-phone-layout";

export function LayoutPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="主屏布局"
        subtitle="拖动的是手机上的模块。地址、Token 和字号在配置里。"
      />
      <PhoneLayoutEditor />
    </div>
  );
}
