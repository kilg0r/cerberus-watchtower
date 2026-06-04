import { createRouter, createWebHistory } from 'vue-router'
import ReviewQueueView from '../views/ReviewQueueView.vue'
import ActivityView from '../views/ActivityView.vue'

const routes = [
  { path: '/', redirect: '/review' },
  { path: '/review', name: 'review', component: ReviewQueueView },
  { path: '/activity', name: 'activity', component: ActivityView },
  {
    path: '/architecture',
    name: 'architecture',
    // lazy: keeps cytoscape out of the main bundle
    component: () => import('../views/ArchitectureView.vue'),
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
