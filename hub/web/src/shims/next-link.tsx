import { forwardRef, type AnchorHTMLAttributes, type ReactNode } from "react";

type Props = AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; children?: ReactNode };

const Link = forwardRef<HTMLAnchorElement, Props>(function Link({ href, children, ...props }, ref) {
  return (
    <a ref={ref} href={href} {...props}>
      {children}
    </a>
  );
});

export default Link;
