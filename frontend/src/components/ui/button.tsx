import { Slot } from "@radix-ui/react-slot";
import { type VariantProps, cva } from "class-variance-authority";
import * as React from "react";
import { cn } from "@lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-sm text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-button-primary text-text-primary hover:bg-button-primary/90 border border-text-primary shadow-shadow-primary",
        secondary:
          "bg-button-secondary text-text-secondary hover:bg-button-secondary/90 border border-border-secondary shadow-shadow-secondary",
        ghost:
          "hover:bg-accent text-text-secondary hover:text-accent-foreground",
        link: "text-text-primary underline-offset-4 underline",
        gradient:
          "bg-gradient-button text-text-primary hover:bg-primary/90 border shadow-shadow-primary",
        destructive:
          "bg-button-destructive text-destructive-foreground hover:bg-button-destructive/90 border border-destructive-border ",
        healthy:
          "bg-healthy text-healthy-text hover:bg-healthy/80 border border-healthy-text",
        dropdown: "bg-textarea justify-between text-start border",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-8 rounded px-3",
        lg: "h-12 rounded-lg px-8",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
