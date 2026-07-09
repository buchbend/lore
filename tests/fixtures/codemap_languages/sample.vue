<template>
  <div>{{ label() }}</div>
</template>

<script setup>
function label() {
  return helper();
}

function helper() {
  return "hi";
}
</script>
