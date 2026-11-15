export function recordingName(value: string): string {
  const name = value.trim()
  if (!name) throw new Error('请输入名称')
  if ([...name].length > 80 || new TextEncoder().encode(name).length > 180) {
    throw new Error('名称过长，请缩短后重试')
  }
  if (/[<>:"/\\|?*\u0000-\u001f\u007f-\u009f]/.test(name) || name.endsWith('.')) {
    throw new Error('名称不能包含路径分隔符、控制字符或 <>:"|?*，也不能以句点结尾')
  }
  return name
}
