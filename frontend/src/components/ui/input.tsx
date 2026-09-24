import * as React from "react";
import { cn } from "@lib/utils";

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex h-8 w-full rounded-esm border border-muted bg-input px-3 py-2 text-base placeholder:text-muted-foreground focus:border-border focus-visible:outline-none  focus-visible:ring-ring disabled:cursor-not-allowed disabled:bg-secondary-button disabled:border-border disabled:placeholder:text-foreground file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground md:text-sm",
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);

Input.displayName = "Input";

export { Input };
