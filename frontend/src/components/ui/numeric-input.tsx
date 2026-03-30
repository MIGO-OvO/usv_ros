import * as React from "react"
import { cn } from "@/lib/utils"

interface NumericInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'value' | 'type'> {
  value: number | string
  onValueChange: (value: number) => void
  integer?: boolean
}

/**
 * 数值输入组件 — 解决前导零、NaN、焦点选中问题
 * - 聚焦时选中全部文本
 * - 失焦时清理前导零和无效值
 * - 支持整数/浮点两种模式
 */
const NumericInput = React.forwardRef<HTMLInputElement, NumericInputProps>(
  ({ className, value, onValueChange, integer = false, ...props }, ref) => {
    const [localValue, setLocalValue] = React.useState(String(value))

    React.useEffect(() => {
      setLocalValue(String(value))
    }, [value])

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value
      setLocalValue(raw)
      const num = integer ? parseInt(raw, 10) : parseFloat(raw)
      if (!isNaN(num)) {
        onValueChange(num)
      }
    }

    const handleBlur = () => {
      const num = integer ? parseInt(localValue, 10) : parseFloat(localValue)
      if (isNaN(num)) {
        setLocalValue('0')
        onValueChange(0)
      } else {
        setLocalValue(String(num))
        onValueChange(num)
      }
    }

    const handleFocus = (e: React.FocusEvent<HTMLInputElement>) => {
      e.target.select()
    }

    return (
      <input
        ref={ref}
        type="text"
        inputMode={integer ? "numeric" : "decimal"}
        className={cn(
          "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        value={localValue}
        onChange={handleChange}
        onBlur={handleBlur}
        onFocus={handleFocus}
        {...props}
      />
    )
  }
)
NumericInput.displayName = "NumericInput"

export { NumericInput }

