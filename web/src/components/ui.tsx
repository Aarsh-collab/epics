import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../lib/utils';
export const buttonVariants = cva('button', { variants: { variant: { default: 'button-primary', outline: 'button-outline', ghost: 'button-ghost' } }, defaultVariants: { variant: 'default' } });
export function Button({ className, variant, asChild = false, ...props }: React.ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) { const Comp = asChild ? Slot : 'button'; return <Comp className={cn(buttonVariants({ variant }), className)} {...props}/>; }
export function Card({ className, ...props }: React.ComponentProps<'section'>) { return <section className={cn('card', className)} {...props}/>; }
export function CardHeader({ className, ...props }: React.ComponentProps<'header'>) { return <header className={cn('card-header', className)} {...props}/>; }
export function CardTitle(props: React.ComponentProps<'h2'>) { return <h2 {...props}/>; }
export function CardDescription(props: React.ComponentProps<'p'>) { return <p className="muted" {...props}/>; }
export function CardContent({ className, ...props }: React.ComponentProps<'div'>) { return <div className={cn('card-content', className)} {...props}/>; }
export function Badge({ className, ...props }: React.ComponentProps<'span'>) { return <span className={cn('badge', className)} {...props}/>; }
